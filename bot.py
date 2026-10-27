import os
import re
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
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
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
    MessageHandler,
    filters,
)

from parser_rasp import get_schedule_for_date, get_schedule_for_week

BOT_TOKEN = "8970456575:AAFUoFW-hT0pcNJE0mfE3Xzx_Wq9-pkFU40"
DEFAULT_GROUP = "ПИЭ-26-2"
ADMIN_ID = 1087118817
TIMEZONE = ZoneInfo("Asia/Yekaterinburg")

BANNERS = {
    "main": "banners/main.png",
    "today": "banners/today.png",
    "tomorrow": "banners/tomorrow.png",
    "week": "banners/week.png",
    "date": "banners/date.png",
    "subgroup": "banners/subgroup.png",
    "feedback": "banners/feedback.png",
}

def get_main_keyboard(subgroup: int = None) -> InlineKeyboardMarkup:
    """Формирует главное меню: если подгруппа не выбрана, показывает кнопку добавления."""
    buttons = [
        [
            InlineKeyboardButton("📅 Сегодня", callback_data="today"),
            InlineKeyboardButton("📆 Завтра", callback_data="tomorrow"),
        ],
        [
            InlineKeyboardButton("🗓 Текущая неделя", callback_data="week_current"),
            InlineKeyboardButton("📆 Выбрать день", callback_data="pick_date"),
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

def get_welcome_text(user_name: str, subgroup: int = None) -> str:
    """Формирует текст главного экрана меню с учетом подгруппы."""
    subgroup_str = f" (Подгруппа {subgroup})" if subgroup else ""
    return (
        f"👋 Привет, {user_name}!\n\n"
        f"🏛 **Бот расписания УрГЭУ**\n"
        f"👥 Группа: `{DEFAULT_GROUP}`{subgroup_str}\n\n"
        f"Выбери нужный раздел в меню ниже 👇"
    )

async def start_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Обработчик команды /start."""
    context.user_data["waiting_for_date"] = False
    context.user_data["waiting_for_feedback"] = False

    subgroup = context.user_data.get("subgroup")
    user_name = update.effective_user.first_name
    welcome_text = get_welcome_text(user_name, subgroup)

    temp_msg = await update.message.reply_text("⏳", reply_markup=ReplyKeyboardRemove())
    await temp_msg.delete()

    with open(BANNERS["main"], "rb") as photo:
        await update.message.reply_photo(
            photo=photo,
            caption=welcome_text,
            parse_mode="Markdown",
            reply_markup=get_main_keyboard(subgroup),
        )

async def handle_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Обработчик нажатий на Inline-кнопки с бесшовной сменой баннера и текста."""
    query = update.callback_query
    await query.answer()

    data = query.data
    today = datetime.now(TIMEZONE)
    subgroup = context.user_data.get("subgroup")
    banner_key = "main"

    if data == "main_menu":
        context.user_data["waiting_for_date"] = False
        context.user_data["waiting_for_feedback"] = False
        user_name = query.from_user.first_name
        text = get_welcome_text(user_name, subgroup)
        reply_markup = get_main_keyboard(subgroup)
        banner_key = "main"

    elif data == "choose_subgroup":
        context.user_data["waiting_for_date"] = False
        context.user_data["waiting_for_feedback"] = False
        text = (
            "👥 **Выбор подгруппы**\n\n"
            "Выберите вашу подгруппу (1 или 2).\n"
            "После этого бот будет показывать только ваши пары:"
        )
        reply_markup = SUBGROUP_KEYBOARD
        banner_key = "subgroup"

    elif data == "set_subgroup_1":
        context.user_data["subgroup"] = 1
        subgroup = 1
        user_name = query.from_user.first_name
        text = (
            "✅ **Подгруппа 1 успешно установлена!**\n\n"
            f"{get_welcome_text(user_name, subgroup)}"
        )
        reply_markup = get_main_keyboard(subgroup)
        banner_key = "main"

    elif data == "set_subgroup_2":
        context.user_data["subgroup"] = 2
        subgroup = 2
        user_name = query.from_user.first_name
        text = (
            "✅ **Подгруппа 2 успешно установлена!**\n\n"
            f"{get_welcome_text(user_name, subgroup)}"
        )
        reply_markup = get_main_keyboard(subgroup)
        banner_key = "main"

    elif data == "feedback":
        context.user_data["waiting_for_date"] = False
        context.user_data["waiting_for_feedback"] = True
        context.user_data["menu_message_id"] = query.message.message_id
        text = (
            "💬 **Поддержка и предложения**\n\n"
            "Напишите ваши идеи, отзывы или найденные ошибки следующим сообщением. "
            "Разработчик обязательно их прочитает!"
        )
        reply_markup = SCHEDULE_KEYBOARD
        banner_key = "feedback"

    elif data == "pick_date":
        context.user_data["waiting_for_feedback"] = False
        context.user_data["waiting_for_date"] = True
        context.user_data["menu_message_id"] = query.message.message_id
        text = (
            "✍️ **Введите число и месяц** в формате `ДД.ММ`\n\n"
            "Например: `28.10` или `05.11`\n\n"
            "Или нажмите кнопку ниже для отмены:"
        )
        reply_markup = SCHEDULE_KEYBOARD
        banner_key = "date"

    else:
        context.user_data["waiting_for_date"] = False
        context.user_data["waiting_for_feedback"] = False

        if data == "today":
            text = get_schedule_for_date(DEFAULT_GROUP, today, subgroup=subgroup)
            banner_key = "today"
        elif data == "tomorrow":
            target = today + timedelta(days=1)
            text = get_schedule_for_date(DEFAULT_GROUP, target, subgroup=subgroup)
            banner_key = "tomorrow"
        elif data in ("week_current", "week"):
            text = get_schedule_for_week(DEFAULT_GROUP, next_week=False, subgroup=subgroup)
            banner_key = "week"
        else:
            text = "Неизвестная команда."
            banner_key = "main"

        reply_markup = SCHEDULE_KEYBOARD

    if len(text) > 1000:
        text = text[:950].rsplit("\n", 1)[0] + "\n\n*(показана часть расписания)*"

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
            print(f"Ошибка при смене баннера: {e}")

async def handle_user_text(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Обработка ввода пользователя (даты, обратная связь) и ответов админа."""
    user_input = update.message.text.strip()
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    # --- 1. ЕСЛИ ПИШЕТ АДМИН (ОТВЕТ НА ОБРАЩЕНИЕ ЧЕРЕЗ REPLY) ---
    if user_id == ADMIN_ID and update.message.reply_to_message:
        replied_msg = update.message.reply_to_message
        replied_text = replied_msg.text or replied_msg.caption or ""

        # Ищем ID пользователя в цитируемом сообщении
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

    # --- 2. ОБРАБОТКА ОБРАЩЕНИЯ В ПОДДЕРЖКУ ОТ ПОЛЬЗОВАТЕЛЯ ---
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
            f"*(Чтобы ответить, просто ответьте (Reply) на это сообщение)*"
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
            "Разработчик обязательно прочитает его и при необходимости ответит вам прямо здесь в боте."
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

    # --- 3. ОБРАБОТКА ВВОДА ДАТЫ ---
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
                schedule_text = schedule_text[:950].rsplit("\n", 1)[0] + "\n\n*(показана часть)*"

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

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_user_text))

    print("✅ Бот успешно запущен и ожидает сообщений!")
    app.run_polling()

if __name__ == '__main__':
    main()

