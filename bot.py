import json
import os
import re
import threading
from datetime import datetime, time, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from zoneinfo import ZoneInfo

from parser_rasp import (
    get_schedule_for_date,
    get_schedule_for_week,
    get_short_schedule_for_date,
)
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    ReplyKeyboardRemove,
    Update,
)
from telegram.error import BadRequest
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

BOT_TOKEN = "8970456575:AAFUoFW-hT0pcNJE0mfE3Xzx_Wq9-pkFU40"
DEFAULT_GROUP = "ПИЭ-26-2"
ADMIN_ID = 1087118817
TIMEZONE = ZoneInfo("Asia/Yekaterinburg")
NOTIF_FILE = "notifications.json"

BANNERS = {
    "main": "banners/main.png",
    "today": "banners/today.png",
    "tomorrow": "banners/tomorrow.png",
    "week": "banners/week.png",
    "date": "banners/date.png",
    "subgroup": "banners/subgroup.png",
    "feedback": "banners/feedback.png",
}

# --- РАБОТА С ФАЙЛОМ УВЕДОМЛЕНИЙ ---
def load_notifications() -> dict:
    if os.path.exists(NOTIF_FILE):
        try:
            with open(NOTIF_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_notifications(data: dict) -> None:
    with open(NOTIF_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# --- КЛАВИАТУРЫ ---
def get_main_keyboard(subgroup: int = None) -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton("📅 Сегодня", callback_data="today"),
            InlineKeyboardButton("📆 Завтра", callback_data="tomorrow"),
        ],
        [
            InlineKeyboardButton("🗓 Текущая неделя", callback_data="week_current"),
            InlineKeyboardButton("📆 Выбрать день", callback_data="pick_date"),
        ],
        [
            InlineKeyboardButton("🔔 Уведомления", callback_data="notif_menu"),
        ],
    ]

    if not subgroup:
        buttons.append([
            InlineKeyboardButton("➕ Добавить подгруппу", callback_data="choose_subgroup")
        ])

    buttons.append([
        InlineKeyboardButton("💬 Поддержка / Идеи", callback_data="feedback")
    ])

    return InlineKeyboardMarkup(buttons)

def get_notif_keyboard(current_time: str = None) -> InlineKeyboardMarkup:
    """Клавиатура выбора времени пуш-уведомлений."""
    times = ["18:00", "19:00", "20:00", "21:00", "22:00"]
    row = []
    for t in times:
        label = f"✅ {t}" if t == current_time else t
        row.append(InlineKeyboardButton(label, callback_data=f"set_notif_{t}"))

    return InlineKeyboardMarkup([
        row[:3],
        row[3:],
        [
            InlineKeyboardButton("🔕 Отключить уведомления", callback_data="set_notif_off")
        ],
        [
            InlineKeyboardButton("◀️ Главное меню", callback_data="main_menu")
        ]
    ])

SUBGROUP_KEYBOARD = InlineKeyboardMarkup([
    [
        InlineKeyboardButton("1️⃣ 1-я подгруппа", callback_data="set_subgroup_1"),
        InlineKeyboardButton("2️⃣ 2-я подгруппа", callback_data="set_subgroup_2"),
    ],
    [
        InlineKeyboardButton("◀️ Главное меню", callback_data="main_menu"),
    ]
])

SCHEDULE_KEYBOARD = InlineKeyboardMarkup([
    [
        InlineKeyboardButton("◀️ Главное меню", callback_data="main_menu"),
    ]
])

WEEK_KEYBOARD = InlineKeyboardMarkup([
    [
        InlineKeyboardButton("◀️ Назад в главное меню", callback_data="close_week"),
    ]
])

def get_welcome_text(user_name: str, subgroup: int = None) -> str:
    subgroup_str = f" `[Подгруппа {subgroup}]`" if subgroup else ""
    return (
        f"👋 Рады видеть, *{user_name}*!\n\n"
        f"🏛 **УрГЭУ | РАСПИСАНИЕ**\n"
        f"📚 Группа: `{DEFAULT_GROUP}`{subgroup_str}\n"
        f"────────────────────\n"
        f"Выберите действие в меню:"
    )

# --- ФОНОВАЯ ОТПРАВКА УВЕДОМЛЕНИЙ ---
async def send_daily_notifications(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Проверяет пользователей каждую минуту и присылает пуш в заданное время."""
    now = datetime.now(TIMEZONE)
    current_time_str = now.strftime("%H:%M")

    notifs = load_notifications()
    if not notifs:
        return

    tomorrow = now + timedelta(days=1)
    tomorrow_date_str = tomorrow.strftime("%d.%m.%Y")

    for user_id_str, user_info in list(notifs.items()):
        if not user_info.get("enabled", False):
            continue

        if user_info.get("time") == current_time_str:
            chat_id = int(user_id_str)
            subgroup = user_info.get("subgroup")

            short_text = get_short_schedule_for_date(DEFAULT_GROUP, tomorrow, subgroup=subgroup)

            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "📖 Полное расписание",
                        callback_data=f"show_full_{tomorrow_date_str}"
                    )
                ]
            ])

            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=short_text,
                    parse_mode="Markdown",
                    reply_markup=keyboard,
                )
            except Exception as e:
                print(f"Ошибка отправки уведомления пользователю {chat_id}: {e}")


async def start_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    context.user_data["waiting_for_date"] = False
    context.user_data["waiting_for_feedback"] = False

    subgroup = context.user_data.get("subgroup")
    user_name = update.effective_user.first_name
    welcome_text = get_welcome_text(user_name, subgroup)

    temp_msg = await update.message.reply_text("⏳", reply_markup=ReplyKeyboardRemove())
    await temp_msg.delete()

    with open(BANNERS["main"], "rb") as photo:
        sent_msg = await update.message.reply_photo(
            photo=photo,
            caption=welcome_text,
            parse_mode="Markdown",
            reply_markup=get_main_keyboard(subgroup),
        )
        context.user_data["main_menu_msg_id"] = sent_msg.message_id


async def handle_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    await query.answer()

    data = query.data
    chat_id = query.message.chat_id
    today = datetime.now(TIMEZONE)
    subgroup = context.user_data.get("subgroup")
    banner_key = "main"

    # 1. Открытие полного расписания из пуш-уведомления
    if data.startswith("show_full_"):
        date_str = data.replace("show_full_", "")
        target_date = datetime.strptime(date_str, "%d.%m.%Y").replace(tzinfo=TIMEZONE)
        full_text = get_schedule_for_date(DEFAULT_GROUP, target_date, subgroup=subgroup)
        await query.message.reply_text(
            full_text,
            parse_mode="Markdown",
            reply_markup=WEEK_KEYBOARD
        )
        return

    # 2. Закрытие недельного расписания
    if data == "close_week":
        try:
            await query.message.delete()
        except Exception:
            pass

        main_menu_id = context.user_data.get("main_menu_msg_id")
        if main_menu_id:
            user_name = query.from_user.first_name
            text = get_welcome_text(user_name, subgroup)
            with open(BANNERS["main"], "rb") as photo:
                try:
                    await context.bot.edit_message_media(
                        chat_id=chat_id,
                        message_id=main_menu_id,
                        media=InputMediaPhoto(media=photo, caption=text, parse_mode="Markdown"),
                        reply_markup=get_main_keyboard(subgroup),
                    )
                except BadRequest:
                    pass
        return

    context.user_data["main_menu_msg_id"] = query.message.message_id

    # 3. Меню настроек уведомлений
    if data == "notif_menu":
        notifs = load_notifications()
        user_info = notifs.get(str(chat_id), {})
        is_enabled = user_info.get("enabled", False)
        saved_time = user_info.get("time", "не выбрано")

        status_icon = "🟢 Включены" if is_enabled else "🔴 Выключены"
        text = (
            "🔔 **НАСТРОЙКА УВЕДОМЛЕНИЙ**\n"
            "────────────────────\n"
            f"Статус: *{status_icon}*\n"
            f"Время отправки: *{saved_time}*\n\n"
            "Каждый день бот пришлет лаконичный пуш с парами на завтра. "
            "Выберите удобное время:"
        )
        reply_markup = get_notif_keyboard(saved_time if is_enabled else None)
        banner_key = "main"

    elif data.startswith("set_notif_"):
        notifs = load_notifications()
        chosen = data.replace("set_notif_", "")

        if chosen == "off":
            if str(chat_id) in notifs:
                notifs[str(chat_id)]["enabled"] = False
                save_notifications(notifs)
            text = (
                "🔕 **Уведомления отключены.**\n\n"
                "Вы можете включить их в любой момент, выбрав время ниже:"
            )
            reply_markup = get_notif_keyboard(None)
        else:
            notifs[str(chat_id)] = {
                "enabled": True,
                "time": chosen,
                "subgroup": subgroup,
            }
            save_notifications(notifs)
            text = (
                f"✅ **Уведомления настроены на {chosen}!**\n\n"
                f"Каждый вечер в *{chosen}* бот будет присылать список пар на следующий день."
            )
            reply_markup = get_notif_keyboard(chosen)

        banner_key = "main"

    # 4. Главное меню
    elif data == "main_menu":
        context.user_data["waiting_for_date"] = False
        context.user_data["waiting_for_feedback"] = False
        user_name = query.from_user.first_name
        text = get_welcome_text(user_name, subgroup)
        reply_markup = get_main_keyboard(subgroup)
        banner_key = "main"

    # 5. Подгруппы
    elif data == "choose_subgroup":
        text = (
            "👥 **ВЫБОР ПОДГРУППЫ**\n"
            "────────────────────\n"
            "Укажите номер вашей подгруппы (1 или 2):"
        )
        reply_markup = SUBGROUP_KEYBOARD
        banner_key = "subgroup"

    elif data in ("set_subgroup_1", "set_subgroup_2"):
        val = 1 if data == "set_subgroup_1" else 2
        context.user_data["subgroup"] = val
        subgroup = val

        # Синхронизируем подгруппу с базой уведомлений
        notifs = load_notifications()
        if str(chat_id) in notifs:
            notifs[str(chat_id)]["subgroup"] = val
            save_notifications(notifs)

        user_name = query.from_user.first_name
        text = (
            f"✅ **Подгруппа {val} успешно установлена!**\n\n"
            f"{get_welcome_text(user_name, subgroup)}"
        )
        reply_markup = get_main_keyboard(subgroup)
        banner_key = "main"

    # 6. Обратная связь
    elif data == "feedback":
        context.user_data["waiting_for_date"] = False
        context.user_data["waiting_for_feedback"] = True
        context.user_data["menu_message_id"] = query.message.message_id
        text = (
            "💬 **Поддержка и предложения**\n\n"
            "Напишите ваши идеи, отзывы или найденные ошибки следующим сообщением:"
        )
        reply_markup = SCHEDULE_KEYBOARD
        banner_key = "feedback"

    # 7. Выбор дня
    elif data == "pick_date":
        context.user_data["waiting_for_feedback"] = False
        context.user_data["waiting_for_date"] = True
        context.user_data["menu_message_id"] = query.message.message_id
        text = (
            "✍️ **Введите число и месяц** в формате `ДД.ММ`\n\n"
            "Например: `28.10` или `05.11`"
        )
        reply_markup = SCHEDULE_KEYBOARD
        banner_key = "date"

    # 8. Расписание
    else:
        context.user_data["waiting_for_date"] = False
        context.user_data["waiting_for_feedback"] = False

        if data == "today":
            text = get_schedule_for_date(DEFAULT_GROUP, today, subgroup=subgroup)
            banner_key = "today"
            reply_markup = SCHEDULE_KEYBOARD

        elif data == "tomorrow":
            target = today + timedelta(days=1)
            text = get_schedule_for_date(DEFAULT_GROUP, target, subgroup=subgroup)
            banner_key = "tomorrow"
            reply_markup = SCHEDULE_KEYBOARD

        elif data in ("week_current", "week"):
            banner_key = "week"
            text = "🗓 **Расписание на текущую неделю сформировано отдельным сообщением ниже 👇**"
            reply_markup = SCHEDULE_KEYBOARD
            full_week_text = get_schedule_for_week(DEFAULT_GROUP, next_week=False, subgroup=subgroup)
            await query.message.reply_text(
                full_week_text,
                parse_mode="Markdown",
                reply_markup=WEEK_KEYBOARD,
            )

        else:
            text = "Неизвестная команда."
            banner_key = "main"
            reply_markup = SCHEDULE_KEYBOARD

    if len(text) > 1000:
        text = text[:950].rsplit("\n", 1)[0] + "..."

    photo_path = BANNERS.get(banner_key, BANNERS["main"])

    try:
        with open(photo_path, "rb") as photo:
            await query.edit_message_media(
                media=InputMediaPhoto(media=photo, caption=text, parse_mode="Markdown"),
                reply_markup=reply_markup,
            )
    except BadRequest as e:
        error_msg = str(e)
        if "Can't parse entities" in error_msg:
            with open(photo_path, "rb") as photo:
                await query.edit_message_media(
                    media=InputMediaPhoto(media=photo, caption=text),
                    reply_markup=reply_markup,
                )
        elif "Message is not modified" not in error_msg:
            print(f"Ошибка смены баннера: {e}")


async def handle_user_text(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    user_input = update.message.text.strip()
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    # Реплай админа
    if user_id == ADMIN_ID and update.message.reply_to_message:
        replied_msg = update.message.reply_to_message
        replied_text = replied_msg.text or replied_msg.caption or ""

        match = re.search(r"ID:\s*`?(\d+)`?", replied_text)
        if match:
            target_user_id = int(match.group(1))
            answer_for_user = (
                "📩 **Ответ от разработчика:**\n\n"
                f"{user_input}\n\n"
                "_Если у вас остались вопросы, вы всегда можете написать в Поддержку снова._"
            )
            try:
                await context.bot.send_message(
                    chat_id=target_user_id,
                    text=answer_for_user,
                    parse_mode="Markdown",
                )
                await update.message.reply_text("✅ Ответ успешно доставлен пользователю!")
            except Exception as e:
                await update.message.reply_text(f"❌ Не удалось доставить ответ: {e}")
            return

    # Обращение в поддержку
    if context.user_data.get("waiting_for_feedback"):
        try:
            await update.message.delete()
        except Exception:
            pass

        menu_id = context.user_data.get("menu_message_id")
        user = update.effective_user
        username = f"@{user.username}" if user.username else "нет username"

        admin_message = (
            f"📩 **Новое обращение!**\n\n"
            f"👤 От: {user.first_name} ({username})\n"
            f"🆔 ID: `{user.id}`\n\n"
            f"💬 **Текст:**\n{user_input}\n\n"
            f"*(Чтобы ответить, просто сделайте Reply на это сообщение)*"
        )

        try:
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=admin_message,
                parse_mode="Markdown",
            )
        except Exception as e:
            print(f"Ошибка отправки админу: {e}")

        context.user_data["waiting_for_feedback"] = False
        success_text = (
            "✅ **Спасибо! Ваше сообщение отправлено.**\n\n"
            "Разработчик обязательно прочитает его."
        )

        if menu_id:
            try:
                with open(BANNERS["feedback"], "rb") as photo:
                    await context.bot.edit_message_media(
                        chat_id=chat_id,
                        message_id=menu_id,
                        media=InputMediaPhoto(media=photo, caption=success_text, parse_mode="Markdown"),
                        reply_markup=SCHEDULE_KEYBOARD,
                    )
            except BadRequest:
                pass
        return

    # Ввод даты
    if context.user_data.get("waiting_for_date"):
        try:
            await update.message.delete()
        except Exception:
            pass

        menu_id = context.user_data.get("menu_message_id")

        try:
            today = datetime.now(TIMEZONE)
            parsed_date = datetime.strptime(user_input, "%d.%m")
            target_date = parsed_date.replace(year=today.year, tzinfo=TIMEZONE)

            subgroup = context.user_data.get("subgroup")
            schedule_text = get_schedule_for_date(DEFAULT_GROUP, target_date, subgroup=subgroup)
            context.user_data["waiting_for_date"] = False

            if len(schedule_text) > 1000:
                schedule_text = schedule_text[:950].rsplit("\n", 1)[0] + "..."

            if menu_id:
                try:
                    with open(BANNERS["date"], "rb") as photo:
                        await context.bot.edit_message_media(
                            chat_id=chat_id,
                            message_id=menu_id,
                            media=InputMediaPhoto(media=photo, caption=schedule_text, parse_mode="Markdown"),
                            reply_markup=SCHEDULE_KEYBOARD,
                        )
                except BadRequest:
                    pass
            return

        except ValueError:
            error_text = (
                "⚠️ **Неверный формат даты.**\n\n"
                "Пожалуйста, отправьте дату в виде `ДД.ММ` (например, `28.10`):"
            )
            if menu_id:
                try:
                    with open(BANNERS["date"], "rb") as photo:
                        await context.bot.edit_message_media(
                            chat_id=chat_id,
                            message_id=menu_id,
                            media=InputMediaPhoto(media=photo, caption=error_text, parse_mode="Markdown"),
                            reply_markup=SCHEDULE_KEYBOARD,
                        )
                except BadRequest:
                    pass
            return

    try:
        await update.message.delete()
    except Exception:
        pass


class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, format, *args):
        return

def run_fake_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    server.serve_forever()


def main() -> None:
    threading.Thread(target=run_fake_server, daemon=True).start()

    print("Бот запускается...")
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Запуск планировщика уведомлений: проверка каждую минуту (60 секунд)
    if app.job_queue:
        app.job_queue.run_repeating(send_daily_notifications, interval=60, first=10)

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_user_text))

    print("✅ Бот успешно запущен и ожидает сообщений!")
    app.run_polling()


if __name__ == '__main__':
    main()
