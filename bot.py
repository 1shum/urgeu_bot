import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove, Update
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

# Вставь сюда токен, полученный от @BotFather
BOT_TOKEN = "8970456575:AAFUoFW-hT0pcNJE0mfE3Xzx_Wq9-pkFU40"
DEFAULT_GROUP = "ПИЭ-26-2"
ADMIN_ID = 1087118817
TIMEZONE = ZoneInfo("Asia/Yekaterinburg")
# Клавиатура главного меню
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

    # Если подгруппа еще не выбрана — добавляем кнопку
    if not subgroup:
        buttons.append([
            InlineKeyboardButton("➕ Добавить подгруппу", callback_data="choose_subgroup")
        ])

    buttons.append([
        InlineKeyboardButton("💬 Поддержка / Идеи", callback_data="feedback")
    ])

    return InlineKeyboardMarkup(buttons)


# Клавиатура выбора подгруппы
SUBGROUP_KEYBOARD = InlineKeyboardMarkup([
    [
        InlineKeyboardButton("1️⃣ 1-я подгруппа", callback_data="set_subgroup_1"),
        InlineKeyboardButton("2️⃣ 2-я подгруппа", callback_data="set_subgroup_2"),
    ],
    [
        InlineKeyboardButton("◀️ Главное меню", callback_data="main_menu"),
    ]
])




# Клавиатура, которая показывается под расписанием (с кнопкой "Назад")
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
    """Обработчик команды /start при первом запуске."""
    context.user_data["waiting_for_date"] = False
    context.user_data["waiting_for_feedback"] = False

    subgroup = context.user_data.get("subgroup")
    user_name = update.effective_user.first_name
    welcome_text = get_welcome_text(user_name, subgroup)

    temp_msg = await update.message.reply_text("⏳", reply_markup=ReplyKeyboardRemove())
    await temp_msg.delete()

    await update.message.reply_text(
        welcome_text, parse_mode="Markdown", reply_markup=get_main_keyboard(subgroup)
    )





async def handle_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Обработчик нажатий на Inline-кнопки."""
    query = update.callback_query
    await query.answer()

    data = query.data
    today = datetime.now(TIMEZONE)
    subgroup = context.user_data.get("subgroup")

    # Возврат в главное меню
    if data == "main_menu":
        context.user_data["waiting_for_date"] = False
        context.user_data["waiting_for_feedback"] = False
        user_name = query.from_user.first_name
        text = get_welcome_text(user_name, subgroup)
        reply_markup = get_main_keyboard(subgroup)

    # Экран выбора подгруппы
    elif data == "choose_subgroup":
        context.user_data["waiting_for_date"] = False
        context.user_data["waiting_for_feedback"] = False
        text = (
            "👥 **Выбор подгруппы**\n\n"
            "Выберите вашу подгруппу (1 или 2).\n"
            "После выбора бот будет показывать только ваши пары, "
            "а кнопка добавления исчезнет из главного меню:"
        )
        reply_markup = SUBGROUP_KEYBOARD

    # Сохранение выбранной подгруппы 1
    elif data == "set_subgroup_1":
        context.user_data["subgroup"] = 1
        subgroup = 1
        user_name = query.from_user.first_name
        text = (
            "✅ **Подгруппа 1 успешно установлена!**\n\n"
            f"{get_welcome_text(user_name, subgroup)}"
        )
        reply_markup = get_main_keyboard(subgroup)

    # Сохранение выбранной подгруппы 2
    elif data == "set_subgroup_2":
        context.user_data["subgroup"] = 2
        subgroup = 2
        user_name = query.from_user.first_name
        text = (
            "✅ **Подгруппа 2 успешно установлена!**\n\n"
            f"{get_welcome_text(user_name, subgroup)}"
        )
        reply_markup = get_main_keyboard(subgroup)

    # Режим обратной связи
    elif data == "feedback":
        context.user_data["waiting_for_date"] = False
        context.user_data["waiting_for_feedback"] = True
        context.user_data["menu_message_id"] = query.message.message_id
        text = (
            "💬 **Поддержка и предложения**\n\n"
            "Напишите ваш отзыв, найденную ошибку или предложение следующим сообщением.\n\n"
            "Оно будет передано разработчику бота!"
        )
        reply_markup = SCHEDULE_KEYBOARD

    # Режим выбора конкретного дня
    elif data == "pick_date":
        context.user_data["waiting_for_feedback"] = False
        context.user_data["waiting_for_date"] = True
        context.user_data["menu_message_id"] = query.message.message_id
        text = (
            "✍️ **Введите число и месяц** нужного дня в формате `ДД.ММ`\n\n"
            "Например: `28.10` или `05.11`\n\n"
            "Или нажмите кнопку ниже для отмены:"
        )
        reply_markup = SCHEDULE_KEYBOARD

    # Просмотр стандартного расписания
    else:
        context.user_data["waiting_for_date"] = False
        context.user_data["waiting_for_feedback"] = False
        if data == "today":
            text = get_schedule_for_date(DEFAULT_GROUP, today, subgroup=subgroup)
        elif data == "tomorrow":
            target = today + timedelta(days=1)
            text = get_schedule_for_date(DEFAULT_GROUP, target, subgroup=subgroup)
        elif data == "week_current":
            text = get_schedule_for_week(DEFAULT_GROUP, next_week=False, subgroup=subgroup)
        else:
            text = "Неизвестная команда."

        reply_markup = SCHEDULE_KEYBOARD


    try:
        await query.edit_message_text(
            text=text,
            parse_mode="Markdown",
            reply_markup=reply_markup,
        )
    except BadRequest as e:
        if "Message is not modified" not in str(e):
            raise e

async def handle_user_text(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Обработка текстового сообщения с датой или отзывом и автоудаление."""
    user_input = update.message.text.strip()
    chat_id = update.effective_chat.id

    # 1. Если пользователь отправляет сообщение в поддержку
    if context.user_data.get("waiting_for_feedback"):
        try:
            await update.message.delete()
        except Exception:
            pass

        menu_id = context.user_data.get("menu_message_id")
        user = update.effective_user
        username = f"@{user.username}" if user.username else "нет username"

        admin_message = (
            f"📩 **Новое обращение в поддержку!**\n\n"
            f"👤 От: {user.first_name} ({username})\n"
            f"🆔 ID: `{user.id}`\n\n"
            f"💬 **Текст:**\n{user_input}"
        )

        # Отправка разработчику в ЛС
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
            "Разработчик обязательно его прочитает."
        )

        if menu_id:
            try:
                await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=menu_id,
                    text=success_text,
                    parse_mode="Markdown",
                    reply_markup=SCHEDULE_KEYBOARD,
                )
            except BadRequest:
                pass
        return

    # 2. Если пользователь вводит дату (ДД.ММ)
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


            if menu_id:
                await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=menu_id,
                    text=schedule_text,
                    parse_mode="Markdown",
                    reply_markup=SCHEDULE_KEYBOARD,
                )
            else:
                await update.message.reply_text(
                    schedule_text,
                    parse_mode="Markdown",
                    reply_markup=SCHEDULE_KEYBOARD,
                )
            return

        except ValueError:
            error_text = (
                "⚠️ Неверный формат даты.\n\n"
                "Пожалуйста, отправьте дату в виде `ДД.ММ` (например, `28.10`):"
            )
            if menu_id:
                try:
                    await context.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=menu_id,
                        text=error_text,
                        parse_mode="Markdown",
                        reply_markup=SCHEDULE_KEYBOARD,
                    )
                except BadRequest:
                    pass
            return

    # Если пришёл сторонний текст вне режимов ввода — просто удаляем
    try:
        await update.message.delete()
    except Exception:
        pass




class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")


class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, format, *args):
        # Заглушаем постоянные логи проверок от Render, чтобы не спамить в консоль
        return


def run_fake_server():
    # Render передает нужный порт в переменной окружения PORT, по умолчанию берем 10000
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
