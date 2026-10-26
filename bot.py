from datetime import datetime, timedelta
from telegram import ReplyKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from parser_rasp import get_schedule_for_date, get_schedule_for_week

# Вставь сюда токен, полученный от @BotFather
BOT_TOKEN = "8970456575:AAHvSbWVL3CSri_lrOmapCRcPsbmZyul3FY"
DEFAULT_GROUP = "ПИЭ-26-2"

# Клавиатура с быстрыми кнопками
KEYBOARD = [
    ["Сегодня", "Завтра"],
    ["Вся неделя", "След. неделя"],
]
REPLY_MARKUP = ReplyKeyboardMarkup(
    KEYBOARD, resize_keyboard=True, is_persistent=True
)


async def start_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Обработчик команды /start."""
    user_name = update.effective_user.first_name
    welcome_text = (
        f"Привет, {user_name}! 👋\n\n"
        f"Я бот с актуальным расписанием пар УрГЭУ.\n"
        f"Текущая группа: `{DEFAULT_GROUP}`\n\n"
        f"Выбери нужный день на кнопках ниже 👇"
    )
    await update.message.reply_text(
        welcome_text, parse_mode="Markdown", reply_markup=REPLY_MARKUP
    )


async def handle_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Обработчик нажатий на кнопки."""
    text = update.message.text.strip().lower()
    today = datetime.now()

    if text == "сегодня":
        msg = get_schedule_for_date(DEFAULT_GROUP, today)
        await update.message.reply_text(msg, parse_mode="Markdown")

    elif text == "завтра":
        target = today + timedelta(days=1)
        msg = get_schedule_for_date(DEFAULT_GROUP, target)
        await update.message.reply_text(msg, parse_mode="Markdown")

    elif text in ["вся неделя", "текущая неделя"]:
        msg = get_schedule_for_week(DEFAULT_GROUP, next_week=False)
        await update.message.reply_text(msg, parse_mode="Markdown")

    elif text in ["след. неделя", "следующая неделя"]:
        msg = get_schedule_for_week(DEFAULT_GROUP, next_week=True)
        await update.message.reply_text(msg, parse_mode="Markdown")

    else:
        await update.message.reply_text(
            "Пожалуйста, используй кнопки внизу для выбора расписания.",
            reply_markup=REPLY_MARKUP,
        )


def main() -> None:njk
    print("Бот запускается...")
    app = ApplicationBuilder().token("8970456575:AAHvSbWVL3CSri_lrOmapCRcPsbmZyul3FY").build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )

    print("✅ Бот успешно запущен и ожидает сообщений!")
    app.run_polling()


if __name__ == "__main__":
    main()
