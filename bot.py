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
TIMEZONE = ZoneInfo("Asia/Yekaterinburg")
# Клавиатура главного меню
MAIN_MENU_KEYBOARD = InlineKeyboardMarkup([
    [
        InlineKeyboardButton("📅 Сегодня", callback_data="today"),
        InlineKeyboardButton("📆 Завтра", callback_data="tomorrow"),
    ],
    [
        InlineKeyboardButton("🗓 Текущая неделя", callback_data="week_current"),
        InlineKeyboardButton("📆 Выбрать день", callback_data="pick_date"),
    ],
])


# Клавиатура, которая показывается под расписанием (с кнопкой "Назад")
SCHEDULE_KEYBOARD = InlineKeyboardMarkup([
    [
        InlineKeyboardButton("◀️ Главное меню", callback_data="main_menu"),
    ]
])




def get_welcome_text(user_name: str) -> str:
    """Формирует текст главного экрана меню."""
    return (
        f"👋 Привет, {user_name}!\n\n"
        f"🏛 **Бот расписания УрГЭУ**\n"
        f"👥 Группа: `{DEFAULT_GROUP}`\n\n"
        f"Выбери нужный раздел в меню ниже 👇"
    )


async def start_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Обработчик команды /start при первом запуске."""
    context.user_data["waiting_for_date"] = False
    user_name = update.effective_user.first_name
    welcome_text = get_welcome_text(user_name)

    # Убираем старую нижнюю панель с кнопками
    temp_msg = await update.message.reply_text("⏳", reply_markup=ReplyKeyboardRemove())
    await temp_msg.delete()

    # Отправляем главное меню
    await update.message.reply_text(
        welcome_text, parse_mode="Markdown", reply_markup=MAIN_MENU_KEYBOARD
    )




async def handle_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Обработчик нажатий на Inline-кнопки."""
    query = update.callback_query
    await query.answer()

    data = query.data
    today = datetime.now(TIMEZONE)

    # Если нажали "Назад в главное меню"
    if data == "main_menu":
        context.user_data["waiting_for_date"] = False
        user_name = query.from_user.first_name
        text = get_welcome_text(user_name)
        reply_markup = MAIN_MENU_KEYBOARD

    # Если нажали "Выбрать день"
    elif data == "pick_date":
        context.user_data["waiting_for_date"] = True
        context.user_data["menu_message_id"] = query.message.message_id
        text = (
            "✍️ **Введите число и месяц** нужного дня в формате `ДД.ММ`\n\n"
            "Например: `28.10` или `05.11`\n\n"
            "Или нажмите кнопку ниже для отмены:"
        )
        reply_markup = SCHEDULE_KEYBOARD


    # Если выбрали просмотр готовых кнопок
    else:
        context.user_data["waiting_for_date"] = False
        if data == "today":
            text = get_schedule_for_date(DEFAULT_GROUP, today)
        elif data == "tomorrow":
            target = today + timedelta(days=1)
            text = get_schedule_for_date(DEFAULT_GROUP, target)
        elif data == "week_current":
            text = get_schedule_for_week(DEFAULT_GROUP, next_week=False)
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
    """Обработка текстового сообщения с датой и его автоудаление."""
    user_input = update.message.text.strip()
    chat_id = update.effective_chat.id

    # Проверяем, ожидал ли бот ввод даты
    if context.user_data.get("waiting_for_date"):
        # Удаляем сообщение пользователя с датой, чтобы не засорять чат
        try:
            await update.message.delete()
        except Exception:
            pass

        menu_id = context.user_data.get("menu_message_id")

        try:
            today = datetime.now(TIMEZONE)
            # Парсим число и месяц
            parsed_date = datetime.strptime(user_input, "%d.%m")
            target_date = parsed_date.replace(year=today.year, tzinfo=TIMEZONE)

            schedule_text = get_schedule_for_date(DEFAULT_GROUP, target_date)
            context.user_data["waiting_for_date"] = False

            # Редактируем старое меню вместо отправки нового сообщения
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

    # Если пользователь прислал случайный текст вне режима ввода даты — удаляем сообщение
    try:
        await update.message.delete()
    except Exception:
        pass




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
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_user_text))

    print("✅ Бот успешно запущен и ожидает сообщений!")
    app.run_polling()



if __name__ == '__main__':
    main()
