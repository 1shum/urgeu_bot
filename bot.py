import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timedelta
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

# Inline-клавиатура под сообщением
INLINE_KEYBOARD = [
    [
        InlineKeyboardButton("Сегодня", callback_data="today"),
        InlineKeyboardButton("Завтра", callback_data="tomorrow"),
    ],
    [
        InlineKeyboardButton("Вся неделя", callback_data="week_current"),
        InlineKeyboardButton("След. неделя", callback_data="week_next"),
    ],
]
REPLY_MARKUP = InlineKeyboardMarkup(INLINE_KEYBOARD)



async def start_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Обработчик команды /start."""
    user_name = update.effective_user.first_name
    welcome_text = (
        f"Привет, {user_name}! 👋\n\n"
        f"Я бот с актуальным расписанием пар УрГЭУ.\n"
        f"Текущая группа: `{DEFAULT_GROUP}`\n\n"
        f"Выбери нужный день ниже 👇"
    )
    # Принудительно убираем старую нижнюю клавиатуру
    temp_msg = await update.message.reply_text("🧹", reply_markup=ReplyKeyboardRemove())
    await temp_msg.delete()

    # Отправляем меню только с inline-кнопками
    await update.message.reply_text(
        welcome_text, parse_mode="Markdown", reply_markup=REPLY_MARKUP
    )



async def handle_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Обработчик нажатий на кнопки с редактированием сообщения."""
    query = update.callback_query
    await query.answer()

    data = query.data
    today = datetime.now()

    if data == "today":
        msg = get_schedule_for_date(DEFAULT_GROUP, today)
    elif data == "tomorrow":
        target = today + timedelta(days=1)
        msg = get_schedule_for_date(DEFAULT_GROUP, target)
    elif data == "week_current":
        msg = get_schedule_for_week(DEFAULT_GROUP, next_week=False)
    elif data == "week_next":
        msg = get_schedule_for_week(DEFAULT_GROUP, next_week=True)
    else:
        msg = "Неизвестная команда."

    try:
        await query.edit_message_text(
            text=msg,
            parse_mode="Markdown",
            reply_markup=REPLY_MARKUP,
        )
    except BadRequest as e:
        if "Message is not modified" not in str(e):
            raise e



class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")


def run_fake_server():
    server = HTTPServer(('0.0.0.0', 10000), HealthCheckHandler)
    server.serve_forever()


def main() -> None:
    threading.Thread(target=run_fake_server, daemon=True).start()

    print("Бот запускается...")
    app = ApplicationBuilder().token("8970456575:AAFUoFW-hT0pcNJE0mfE3Xzx_Wq9-pkFU40").build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CallbackQueryHandler(handle_callback))


    print("✅ Бот успешно запущен и ожидает сообщений!")
    app.run_polling()


if __name__ == '__main__':
    main()

    main()
