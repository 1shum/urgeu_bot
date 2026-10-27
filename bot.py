import json
import os
import re
import threading
from datetime import datetime, timedelta
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
USERS_FILE = "users.json"

BANNERS = {
    "main": "banners/main.png",
    "today": "banners/today.png",
    "tomorrow": "banners/tomorrow.png",
    "week": "banners/week.png",
    "date": "banners/date.png",
    "subgroup": "banners/subgroup.png",
    "feedback": "banners/feedback.png",
}

# --- РАБОТА С ФАЙЛАМИ JSON ---
def load_json(filepath: str) -> dict:
    if os.path.exists(filepath):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_json(filepath: str, data: dict) -> None:
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_notifications() -> dict:
    return load_json(NOTIF_FILE)

def save_notifications(data: dict) -> None:
    save_json(NOTIF_FILE, data)

def track_user_activity(user, action_name: str) -> None:
    """Фиксирует пользователя и его активность в users.json."""
    if not user:
        return
    users = load_json(USERS_FILE)
    uid = str(user.id)
    now_str = datetime.now(TIMEZONE).strftime("%d.%m.%Y %H:%M")

    if uid not in users:
        users[uid] = {
            "name": user.first_name or "Без имени",
            "username": f"@{user.username}" if user.username else "нет username",
            "first_seen": now_str,
            "last_seen": now_str,
            "actions_count": 1,
            "last_action": action_name,
        }
    else:
        users[uid]["name"] = user.first_name or users[uid].get("name", "Без имени")
        users[uid]["username"] = f"@{user.username}" if user.username else users[uid].get("username", "нет username")
        users[uid]["last_seen"] = now_str
        users[uid]["actions_count"] = users[uid].get("actions_count", 0) + 1
        users[uid]["last_action"] = action_name

    save_json(USERS_FILE, users)

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

# --- КОМАНДЫ ---
async def start_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    track_user_activity(update.effective_user, "/start")

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

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Панель администратора со статистикой активности."""
    if update.effective_user.id != ADMIN_ID:
        return

    users = load_json(USERS_FILE)
    notifs = load_notifications()

    total_users = len(users)
    active_notifs = sum(1 for u in notifs.values() if u.get("enabled"))
    total_actions = sum(u.get("actions_count", 0) for u in users.values())

    sorted_users = sorted(
        users.items(),
        key=lambda item: item[1].get("last_seen", ""),
        reverse=True
    )
