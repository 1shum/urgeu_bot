import json
import os
import re
import threading
from portal import check_portal_auth, get_portal_rating, encrypt_password, decrypt_password, fetch_portal_deadlines
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from zoneinfo import ZoneInfo

from parser_rasp import (
    get_schedule_for_date,
    get_schedule_for_week,
    get_short_schedule_for_date,
    check_group_exists,
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
ADMIN_ID = 1087118817  # здесь твой числовой ID
ADMIN_IDS = [ADMIN_ID]
TIMEZONE = ZoneInfo("Asia/Yekaterinburg")
NOTIF_FILE = "notifications.json"
USERS_FILE = "users.json"
TICKETS_FILE = "tickets.json"

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
def load_tickets() -> dict:
    return load_json(TICKETS_FILE)


def save_tickets(data: dict) -> None:
    save_json(TICKETS_FILE, data)


def get_active_ticket(user_id: int) -> dict | None:
    tickets = load_tickets()
    for t_id, ticket in tickets.items():
        if ticket.get("user_id") == user_id and ticket.get("status") == "open":
            return ticket
    return None


def create_ticket(user_id: int, user_name: str, initial_text: str) -> dict:
    tickets = load_tickets()
    now_str = datetime.now().strftime("%d.%m %H:%M")
    new_id = str(len(tickets) + 1)

    new_ticket = {
        "id": new_id,
        "user_id": user_id,
        "user_name": user_name,
        "status": "open",
        "created_at": now_str,
        "messages": [
            {
                "sender": "user",
                "text": initial_text,
                "time": now_str
            }
        ]
    }
    tickets[new_id] = new_ticket
    save_tickets(tickets)
    return new_ticket


def close_ticket_by_id(ticket_id: str) -> bool:
    tickets = load_tickets()
    if ticket_id in tickets and tickets[ticket_id]["status"] == "open":
        tickets[ticket_id]["status"] = "closed"
        tickets[ticket_id]["closed_at"] = datetime.now().strftime("%d.%m %H:%M")
        save_tickets(tickets)
        return True
    return False

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

async def restore_data_from_telegram(bot) -> None:
    """Восстанавливает users.json из админского чата при холодном рестарте на Render."""
    try:
        chat = await bot.get_chat(ADMIN_ID)
        pinned = chat.pinned_message
        if (
            pinned
            and pinned.document
            and pinned.document.file_name == USERS_FILE
        ):
            file = await bot.get_file(pinned.document.file_id)
            await file.download_to_drive(USERS_FILE)
            print("База users.json успешно восстановлена из Telegram-бэкапа!")
    except Exception as e:
        print(f"Не удалось восстановить базу из Telegram: {e}")


async def sync_backup_to_telegram(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Фоновая задача: отправляет свежий users.json в админский чат и закрепляет его."""
    if not os.path.exists(USERS_FILE):
        return
    try:
        users = load_json(USERS_FILE)
        if not users:
            return

        with open(USERS_FILE, "rb") as f:
            msg = await context.bot.send_document(
                chat_id=ADMIN_ID,
                document=f,
                caption=f"📦 Автобэкап базы ({len(users)} пользователей)\n#backup_users",
                disable_notification=True
            )
            # Закрепляем без звука, чтобы при рестарте бот всегда брал самый свежий файл
            await context.bot.pin_chat_message(
                chat_id=ADMIN_ID,
                message_id=msg.message_id,
                disable_notification=True
            )
    except Exception as e:
        print(f"Ошибка сохранения автобэкапа в Telegram: {e}")

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
            InlineKeyboardButton("👤 Личный кабинет", callback_data="profile_menu"),
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

def get_profile_keyboard(is_portal_linked: bool = False) -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton("📷 Сменить фото профиля", callback_data="profile_change_photo")
        ],
        [
            InlineKeyboardButton("🔄 Сменить / отвязать группу", callback_data="unlink_group")
        ],
        [
            InlineKeyboardButton("👥 Подгруппа", callback_data="choose_subgroup"),
            InlineKeyboardButton("🔔 Уведомления", callback_data="notif_menu")
        ]
    ]

    # Кнопки портала УрГЭУ
    if is_portal_linked:
        buttons.append([
            InlineKeyboardButton("📊 Мой рейтинг", callback_data="portal_rating"),
            InlineKeyboardButton("❌ Отвязать портал", callback_data="portal_unbind")
        ])
    else:
        buttons.append([
            InlineKeyboardButton("🔗 Привязать портал УрГЭУ", callback_data="portal_bind")
        ])

    buttons.append([
        InlineKeyboardButton("◀️ Главное меню", callback_data="main_menu")
    ])
    return InlineKeyboardMarkup(buttons)


def get_support_keyboard(has_active_ticket: bool) -> InlineKeyboardMarkup:
    buttons = []
    if not has_active_ticket:
        buttons.append([InlineKeyboardButton("✍️ Написать обращение", callback_data="ticket_create")])
    else:
        buttons.append([InlineKeyboardButton("💬 Дополнить обращение", callback_data="ticket_add_msg")])
        buttons.append([InlineKeyboardButton("❌ Закрыть обращение", callback_data="ticket_close_self")])

    buttons.append([InlineKeyboardButton("📁 Мои обращения", callback_data="ticket_history")])
    buttons.append([InlineKeyboardButton("◀️ Главное меню", callback_data="main_menu")])
    return InlineKeyboardMarkup(buttons)


def get_user_tickets_keyboard(user_id: int) -> InlineKeyboardMarkup:
    tickets = load_tickets()
    buttons = []
    user_tickets = [t for t in tickets.values() if t.get("user_id") == user_id]

    # Показываем последние 5 обращений
    for t in sorted(user_tickets, key=lambda x: int(x["id"]), reverse=True)[:5]:
        status_icon = "🟡" if t.get("status") == "open" else "✅"
        btn_text = f"{status_icon} Тикет #{t['id']} от {t['created_at']}"
        buttons.append([InlineKeyboardButton(btn_text, callback_data=f"ticket_view_{t['id']}")])

    buttons.append([InlineKeyboardButton("◀️ Назад в поддержку", callback_data="feedback")])
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

WEEK_KEYBOARD = InlineKeyboardMarkup([
    [
        InlineKeyboardButton("◀️ Назад в главное меню", callback_data="close_week"),
    ]
])

def get_profile_data(user_id: int, first_name: str, user_group: str = "Не выбрана") -> tuple[str, str | None]:
    """Возвращает готовый текст профиля и file_id аватарки (если есть)."""
    users = load_json(USERS_FILE)
    notifs = load_notifications()

    u_info = users.get(str(user_id), {})
    n_info = notifs.get(str(user_id), {})

    # Статус группы
    group_str = f"`{user_group}`" if user_group else "не выбрана"
    subgroup = n_info.get("subgroup") or u_info.get("subgroup")
    subgroup_str = f"{subgroup}-я подгруппа" if subgroup else "не выбрана"

    # Статус уведомлений
    is_notif = n_info.get("enabled", False)
    notif_time = n_info.get("time", "—")
    notif_str = f"🟢 Включены ({notif_time})" if is_notif else "🔴 Выключены"

    # Кабинет учащегося (задел на будущее)
    lk_status = "🟢 Подключен" if u_info.get("portal_login") else "⚪ Не подключен"

    # Счетчик действий
    actions = u_info.get("actions_count", 1)
    username = f"@{u_info.get('username', '').lstrip('@')}" if u_info.get("username") else ""

    text = (
        f"👤 **ЛИЧНЫЙ КАБИНЕТ**\n"
        f"🎓 *Группа:* {group_str}\n"
        f"Студент: *{first_name}* {username}\n"
        f"👥 **Подгруппа:** {subgroup_str}\n"
        f"🔔 **Уведомления:** {notif_str}\n"
        f"🎓 **Кабинет учащегося:** {lk_status}\n"
        f"⚡ **Всего действий:** `{actions}`\n"
        f"_Отправьте фото в чат, чтобы установить или сменить аватарку профиля._"
    )
    avatar_file_id = u_info.get("avatar_file_id")
    return text, avatar_file_id

def get_welcome_text(user_name: str, subgroup: int = None, group: str = DEFAULT_GROUP) -> str:
    subgroup_str = f" `[Подгруппа {subgroup}]`" if subgroup else ""
    return (
        f"👋 Рады видеть, *{user_name}*!\n\n"
        f"🏛 **УрГЭУ | РАСПИСАНИЕ**\n"
        f"📚 Группа: `{group}`{subgroup_str}\n"
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

            user_group = user_info.get("user_group") or DEFAULT_GROUP
            short_text = get_short_schedule_for_date(user_group, tomorrow, subgroup=subgroup)
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
    context.user_data["waiting_for_group"] = False

    user_name = update.effective_user.first_name
    chat_id = update.effective_chat.id

    # 1. Если группа ещё не выбрана — показываем экран онбординга (только 1 кнопка)
    users = load_json(USERS_FILE)
    u_info = users.get(str(chat_id), {})
    user_group = context.user_data.get("user_group") or u_info.get("user_group")
    if user_group:
        context.user_data["user_group"] = user_group
    if not user_group:
        temp_msg = await update.message.reply_text("⏳", reply_markup=ReplyKeyboardRemove())
        await temp_msg.delete()

        welcome_new = (
            f"👋 *Привет, {user_name}!* \n\n"
            f"Я бот с расписанием и сервисами для студентов *УрГЭУ* 🎓\n\n"
            f"Для начала работы укажи номер своей учебной группы."
        )
        onboarding_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🎓 Ввести группу", callback_data="onboarding_set_group")]
        ])

        with open(BANNERS["main"], "rb") as photo:
            sent_msg = await update.message.reply_photo(
                photo=photo,
                caption=welcome_new,
                parse_mode="Markdown",
                reply_markup=onboarding_kb
            )
        context.user_data["main_menu_msg_id"] = sent_msg.message_id
        return

    # 2. Если группа уже выбрана — обычное главное меню
    subgroup = context.user_data.get("subgroup")
    welcome_text = get_welcome_text(user_name, subgroup,group=user_group)

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

    lines = [
        "📊 **СТАТИСТИКА БОТА**",
        "────────────────────",
        f"👥 Всего пользователей: *{total_users}*",
        f"🔔 Включили уведомления: *{active_notifs}*",
        f"⚡ Всего действий/кликов: *{total_actions}*",
        "────────────────────",
        "**Последние активные:**"
    ]

    for uid, info in sorted_users[:10]:
        name = info.get("name", "Без имени")
        uname = info.get("username", "")
        seen = info.get("last_seen", "—")
        count = info.get("actions_count", 0)
        act = info.get("last_action", "—")
        lines.append(f"• *{name}* ({uname}) — `{seen}`\n  Кликов: {count} | Посл: `{act}`")

    text = "\n".join(lines)
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📥 Скачать users.json", callback_data="admin_download_db")]
    ])

    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=keyboard)

# --- CALLBACK QUERY HANDLER ---
async def handle_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    try:
        await query.answer()
    except BadRequest:
        pass

    data = query.data
    print(f"[DEBUG CALLBACK RECEIVED]: '{data}'")
    chat_id = query.message.chat_id
    today = datetime.now(TIMEZONE)
    subgroup = context.user_data.get("subgroup")
    banner_key = "main"

    track_user_activity(query.from_user, f"btn:{data}")

    # Выгрузка базы данных в чат для администратора
    if data == "admin_download_db":
        if query.from_user.id == ADMIN_ID and os.path.exists(USERS_FILE):
            with open(USERS_FILE, "rb") as f:
                await context.bot.send_document(
                    chat_id=chat_id,
                    document=f,
                    filename="users.json",
                    caption="📁 Актуальная база пользователей бота."
                )
        return

    # 1. Открытие полного расписания из пуш-уведомления
    if data.startswith("show_full_"):
        date_str = data.replace("show_full_", "")
        target_date = datetime.strptime(date_str, "%d.%m.%Y").replace(tzinfo=TIMEZONE)
        user_group = context.user_data.get("user_group") or DEFAULT_GROUP
        full_text = get_schedule_for_date(user_group, target_date, subgroup=subgroup)
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
            user_group = context.user_data.get("user_group") or DEFAULT_GROUP
            text = get_welcome_text(user_name, subgroup, group=user_group)
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
    # 3. Меню управления всеми уведомлениями (Хаб)
    if data == "notif_menu":
        text = (
            "🔔 **ЦЕНТР УВЕДОМЛЕНИЙ**\n"
            "─\n"
            "Выбери нужный раздел для настройки напоминаний:\n\n"
            "• **Дедлайны (портал)** — новые тесты, лабы и сроки сдачи.\n"
            "• **Уведомления о парах** — вечерняя рассылка расписания на завтра."
        )
        buttons = [
            [InlineKeyboardButton("📝 Дедлайны (портал)", callback_data="deadline_hub")],
            [InlineKeyboardButton("🎓 Уведомления о парах", callback_data="pairs_notif_menu")],
            [InlineKeyboardButton("🔙 В главное меню", callback_data="main_menu")]
        ]
        with open(BANNERS["main"], "rb") as photo:
            await query.edit_message_media(
                media=InputMediaPhoto(media=photo, caption=text, parse_mode="Markdown"),
                reply_markup=InlineKeyboardMarkup(buttons),
            )
        return

    # Подменю настройки пар (твое прежнее меню)
    if data == "pairs_notif_menu":
        notifs = load_notifications()
        user_info = notifs.get(str(chat_id), {})
        is_enabled = user_info.get("enabled", False)
        saved_time = user_info.get("time", "не выбрано")

        status_icon = "🟢 Включены" if is_enabled else "🔴 Выключены"
        text = (
            "🔔 **НАСТРОЙКА УВЕДОМЛЕНИЙ О ПАРАХ**\n"
            "─\n"
            f"Статус: *{status_icon}*\n"
            f"Время отправки: *{saved_time}*\n\n"
            "Каждый день бот пришлет лаконичный пуш с парами на завтра.\n"
            "Выберите удобное время:"
        )
        keyboard_buttons = get_notif_keyboard(saved_time if is_enabled else None).inline_keyboard
        # Добавляем кнопку возврата в хаб уведомлений
        new_kb = list(keyboard_buttons) + [[InlineKeyboardButton("🔙 Назад в уведомления", callback_data="notif_menu")]]

        with open(BANNERS["main"], "rb") as photo:
            await query.edit_message_media(
                media=InputMediaPhoto(media=photo, caption=text, parse_mode="Markdown"),
                reply_markup=InlineKeyboardMarkup(new_kb),
            )
        return
        # Подменю дедлайнов портала
    if data == "deadline_hub":
            users = load_json(USERS_FILE)
            user = users.setdefault(str(chat_id), {})
            settings = user.setdefault("deadline_notify", {"new": True, "24h": True, "3h": True})

            btn_new = "✅ Новые работы" if settings.get("new", True) else "❌ Новые работы"
            btn_24h = "✅ За 24 часа" if settings.get("24h", True) else "❌ За 24 часа"
            btn_3h = "✅ За 3 часа" if settings.get("3h", True) else "❌ За 3 часа"

            text = (
                "📝 **УВЕДОМЛЕНИЯ О ДЕДЛАЙНАХ**\n"
                "─\n"
                "Управляй напоминаниями о тестах и заданиях с портала УрГЭУ:\n\n"
                "• **Новые работы** — бот напишет, если препод выложил тест/задание.\n"
                "• **За 24 часа** — напоминание за сутки до срока.\n"
                "• **За 3 часа** — срочное предупреждение.\n"
            )
            buttons = [
                [InlineKeyboardButton("📋 Посмотреть активные дедлайны", callback_data="show_deadlines_list")],
                [InlineKeyboardButton(btn_new, callback_data="toggle_dl:new"),
                 InlineKeyboardButton(btn_24h, callback_data="toggle_dl:24h")],
                [InlineKeyboardButton(btn_3h, callback_data="toggle_dl:3h")],
                [InlineKeyboardButton("🔙 Назад в уведомления", callback_data="notif_menu")]
            ]
            with open(BANNERS["main"], "rb") as photo:
                await query.edit_message_media(
                    media=InputMediaPhoto(media=photo, caption=text, parse_mode="Markdown"),
                    reply_markup=InlineKeyboardMarkup(buttons),
                )
            return

        # Переключение тумблеров дедлайнов
    if data.startswith("toggle_dl:"):
            key = data.split(":")[1]
            users = load_json(USERS_FILE)
            user = users.setdefault(str(chat_id), {})
            settings = user.setdefault("deadline_notify", {"new": True, "24h": True, "3h": True})
            if key in settings:
                settings[key] = not settings[key]
                save_json(USERS_FILE, users)

            btn_new = "✅ Новые работы" if settings.get("new", True) else "❌ Новые работы"
            btn_24h = "✅ За 24 часа" if settings.get("24h", True) else "❌ За 24 часа"
            btn_3h = "✅ За 3 часа" if settings.get("3h", True) else "❌ За 3 часа"

            text = (
                "📝 **УВЕДОМЛЕНИЯ О ДЕДЛАЙНАХ**\n"
                "─\n"
                "Управляй напоминаниями о тестах и заданиях с портала УрГЭУ:\n\n"
                "• **Новые работы** — бот напишет, если препод выложил тест/задание.\n"
                "• **За 24 часа** — напоминание за сутки до срока.\n"
                "• **За 3 часа** — срочное предупреждение.\n"
            )
            buttons = [
                [InlineKeyboardButton("📋 Посмотреть активные дедлайны", callback_data="show_deadlines_list")],
                [InlineKeyboardButton(btn_new, callback_data="toggle_dl:new"),
                 InlineKeyboardButton(btn_24h, callback_data="toggle_dl:24h")],
                [InlineKeyboardButton(btn_3h, callback_data="toggle_dl:3h")],
                [InlineKeyboardButton("🔙 Назад в уведомления", callback_data="notif_menu")]
            ]
            with open(BANNERS["main"], "rb") as photo:
                await query.edit_message_media(
                    media=InputMediaPhoto(media=photo, caption=text, parse_mode="Markdown"),
                    reply_markup=InlineKeyboardMarkup(buttons),
                )
            return

        # Просмотр списка актуальных дедлайнов прямо из бота
    # Просмотр списка актуальных дедлайнов прямо из бота
    # Просмотр списка актуальных дедлайнов
    # Просмотр списка актуальных дедлайнов
    if data == "show_deadlines_list":
        # 1. Достаем учетные данные пользователя из users.json
        users = load_json(USERS_FILE)
        user = users.get(str(chat_id), {})
        login = user.get("portal_login") or context.user_data.get("portal_login")
        enc_pw = user.get("portal_password") or context.user_data.get("portal_password")

        raw_password = None
        if enc_pw:
            try:
                # Расшифровываем Fernet-токен в чистый пароль
                raw_password = decrypt_password(enc_pw)
            except Exception:
                raw_password = enc_pw

        if not login or not raw_password:
            warn_text = (
                "⚠️ **Портал не привязан!**\n\n"
                "Чтобы просматривать задания и тесты, перейди в **Личный кабинет** и привяжи аккаунт портала УрГЭУ."
            )
            buttons = [
                [InlineKeyboardButton("👤 В Личный кабинет", callback_data="profile_menu")],
                [InlineKeyboardButton("🔙 Назад", callback_data="deadline_hub")]
            ]
            with open(BANNERS["main"], "rb") as photo:
                await query.edit_message_media(
                    media=InputMediaPhoto(media=photo, caption=warn_text, parse_mode="Markdown"),
                    reply_markup=InlineKeyboardMarkup(buttons),
                )
            return

        # Показываем плашку загрузки
        loading_text = (
            "⏳ **Сканирую портал УрГЭУ...**\n"
            "─\n"
            "Проверяю 12 учебных курсов на наличие тестов и заданий.\n"
            "Пожалуйста, подожди несколько секунд..."
        )
        try:
            with open(BANNERS["main"], "rb") as photo:
                await query.edit_message_media(
                    media=InputMediaPhoto(media=photo, caption=loading_text, parse_mode="Markdown"),
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⏳ Загрузка...", callback_data="none")]])
                )
        except Exception as e:
            print(f"[ERROR] Не удалось показать экран загрузки: {e}")

        # 3. Запрашиваем данные с портала (ТЕПЕРЬ ВНЕ БЛОКА EXCEPT)
        try:
            deadlines = await fetch_portal_deadlines(login, raw_password)

            if not deadlines:
                text = (
                    "🎉 **Горящих дедлайнов нет!**\n"
                    "─\n"
                    "Все тесты и задания сданы или преподаватели ещё не выложили новые работы."
                )
            else:
                text = "📋 АКТИВНЫЕ ДЕДЛАЙНЫ С ПОРТАЛА:\n" + ("─" * 28) + "\n\n"
                now = datetime.now()
                for d in deadlines:
                    c_name = d.get("course", "Курс")
                    t_name = d.get("title", "Работа")
                    t_type = d.get("type", "Тест")
                    d_str = d.get("deadline_str") or d.get("raw_date", "Срок не указан")

                    left_str = ""
                    iso_val = d.get("deadline_iso")
                    if iso_val:
                        try:
                            dt = datetime.fromisoformat(iso_val)
                            diff = dt - now
                            days = diff.days
                            hours = int(diff.seconds // 3600)
                            left_str = f" (осталось: {days} дн. {hours} ч.)" if days > 0 else f" (осталось: {hours} ч.)"
                        except Exception:
                            pass

                    icon = "🧪" if "тест" in t_type.lower() else "📝"
                    text += (
                        f"{icon} [{t_type}] {t_name}\n"
                        f"📚 {c_name}\n"
                        f"⏳ Срок: {d_str}{left_str}\n\n"
                    )

            buttons = [
                [InlineKeyboardButton("🔄 Обновить", callback_data="show_deadlines_list")],
                [InlineKeyboardButton("🔙 Назад в настройки", callback_data="deadline_hub")]
            ]

        except Exception as err:
            print(f"[ERROR] Ошибка сбора дедлайнов: {err}")
            text = f"❌ Ошибка при обращении к порталу:\n{err}"
            buttons = [
                [InlineKeyboardButton("🔄 Попробовать снова", callback_data="show_deadlines_list")],
                [InlineKeyboardButton("🔙 Назад в настройки", callback_data="deadline_hub")]
            ]

        if len(text) > 1000:
            await query.message.delete()
            await context.bot.send_message(
                chat_id=chat_id,
                text=text[:4000],
                reply_markup=InlineKeyboardMarkup(buttons)
            )
        else:
            with open(BANNERS["main"], "rb") as photo:
                await query.edit_message_media(
                    media=InputMediaPhoto(media=photo, caption=text),
                    reply_markup=InlineKeyboardMarkup(buttons)
                )
        return
    # 4. Главное меню
        # Личный кабинет
    if data == "profile_menu":
        context.user_data["waiting_for_date"] = False
        context.user_data["waiting_for_feedback"] = False
        context.user_data["waiting_for_avatar"] = False
        context.user_data["waiting_for_portal_login"] = False
        context.user_data["waiting_for_portal_password"] = False

        user_group = context.user_data.get("user_group", "Не выбрана")
        prof_text, avatar_id = get_profile_data(chat_id, query.from_user.first_name, user_group=user_group)

        # 1. ЧИТАЕМ СТАТУС ПОРТАЛА ИЗ БАЗЫ USERS.JSON (и из памяти как резерв)
        users = load_json(USERS_FILE)
        user_entry = users.get(str(chat_id), {})
        db_login = user_entry.get("portal_login") or context.user_data.get("portal_login")
        db_pw = user_entry.get("portal_password") or context.user_data.get("portal_password")

        is_linked = bool(db_login and db_pw)
        reply_markup = get_profile_keyboard(is_linked)

        if avatar_id:
            try:
                await query.edit_message_media(
                    media=InputMediaPhoto(media=avatar_id, caption=prof_text, parse_mode="Markdown"),
                    reply_markup=reply_markup,
                )
                return
            except BadRequest:
                pass
        with open(BANNERS["main"], "rb") as photo:
            try:
                # Если сообщение уже с фото — просто обновляем медиа и текст
                await query.edit_message_media(
                    media=InputMediaPhoto(media=photo, caption=prof_text, parse_mode="Markdown"),
                    reply_markup=reply_markup,
                )
            except Exception:
                # Если кнопка была нажата на обычном тексте (например, после привязки):
                # удаляем текстовое уведомление, чтобы не дублировать меню
                try:
                    await query.message.delete()
                except Exception:
                    pass
                # И обновляем/отправляем профиль в исходное меню
                menu_msg_id = context.user_data.get("menu_message_id")
                if menu_msg_id:
                    try:
                        photo.seek(0)
                        await context.bot.edit_message_media(
                            chat_id=chat_id,
                            message_id=menu_msg_id,
                            media=InputMediaPhoto(media=photo, caption=prof_text, parse_mode="Markdown"),
                            reply_markup=reply_markup
                        )
                        return
                    except Exception:
                        pass
                photo.seek(0)
                sent = await context.bot.send_photo(
                    chat_id=chat_id,
                    photo=photo,
                    caption=prof_text,
                    parse_mode="Markdown",
                    reply_markup=reply_markup
                )
                context.user_data["menu_message_id"] = sent.message_id
        return
        # --- БЛОК ПОРТАЛА УРГЭУ ---
    elif data == "portal_bind":
        context.user_data["waiting_for_portal_login"] = True

        # Отправляем карточку и запоминаем сообщение в переменную prompt_msg
        prompt_msg = await query.message.reply_text(
            "🎓 *Привязка личного кабинета УрГЭУ*\n\nВведи свой ID пользователя от портала (например: `m.m.samosadov`):",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("◀️ Отмена", callback_data="portal_cancel")]
            ])
        )
        # Запоминаем ID этого сообщения, чтобы потом удалить
        context.user_data["portal_prompt_msg_id"] = prompt_msg.message_id
        return
    elif data == "portal_cancel":
        context.user_data["waiting_for_portal_login"] = False
        context.user_data["waiting_for_portal_password"] = False
        context.user_data.pop("portal_prompt_msg_id", None)
        try:
            # Удаляем карточку с вопросом
            await query.message.delete()
        except Exception:
            pass
        return

    elif data == "portal_unbind":
        context.user_data.pop("portal_login", None)
        context.user_data.pop("portal_password", None)
        await query.answer("Личный кабинет портала успешно отвязан!", show_alert=True)

        prof_text, _ = get_profile_data(chat_id, query.from_user.first_name)
        await query.edit_message_caption(caption=prof_text, reply_markup=get_profile_keyboard(False),
                                         parse_mode="Markdown")
        return
    elif data == "unlink_group":
        await query.answer("Группа отвязана")

        # 1. Очищаем группу из оперативной памяти
        context.user_data.pop("user_group", None)
        context.user_data.pop("subgroup", None)
        context.user_data["waiting_for_group"] = True

        # 2. Удаляем группу из файла users.json
        users = load_json(USERS_FILE)
        uid_str = str(chat_id)
        if uid_str in users:
            users[uid_str].pop("user_group", None)
            users[uid_str].pop("subgroup", None)
            save_json(USERS_FILE, users)

        # 3. Отправляем аккуратный запрос на ввод новой группы
        prompt_msg = await context.bot.send_message(
            chat_id=chat_id,
            text="✍️ *Введи номер новой группы:*\n_(Например: `ПИЭ-26-2` или `АИС-26-1`)_",
            parse_mode="Markdown"
        )
        context.user_data["group_prompt_msg_id"] = prompt_msg.message_id
        return

    elif data == "portal_rating":
        login = context.user_data.get("portal_login")
        enc_pw = context.user_data.get("portal_password")

        if not login or not enc_pw:
            await query.answer("Сначала привяжи аккаунт портала!", show_alert=True)
            return

        prev_msg_text = query.message.text or ""
        if "успешно привязан" in prev_msg_text:
            try:
                await query.message.delete()
            except Exception:
                pass
            status_msg = await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="⏳ <b>Подключаюсь к порталу УрГЭУ и считаю баллы...</b>",
                parse_mode="HTML"
            )
        elif "Рейтинг" in prev_msg_text or "академический" in prev_msg_text:
            status_msg = query.message
            await status_msg.edit_text("⏳ <b>Обновляю баллы с портала УрГЭУ...</b>", parse_mode="HTML")
        else:
            status_msg = await query.message.reply_text(
                "⏳ <b>Подключаюсь к порталу УрГЭУ и считаю баллы...</b>",
                parse_mode="HTML"
            )

        try:
            raw_password = decrypt_password(enc_pw)
            success, rating_data, grades_dict = await get_portal_rating(login, raw_password)
        except Exception as e:
            success, rating_data, grades_dict = False, f"Ошибка дешифрования данных: {e}", {}

        if success:
            users = load_json(USERS_FILE)
            uid = str(query.from_user.id)
            prev_grades = users.get(uid, {}).get("last_rating", {})

            lines = ["<b>Текущий академический рейтинг:</b>\n"]
            for item in rating_data:
                subj = item["name"]
                curr_val = item["val"]
                curr_str = item["raw_str"]

                delta_text = ""
                if curr_val is not None and subj in prev_grades:
                    prev_val = prev_grades[subj]
                    diff = round(curr_val - prev_val, 2)
                    if diff > 0:
                        delta_text = f" (📈 +{diff}%)"
                    elif diff < 0:
                        delta_text = f" (📉 {diff}%)"

                lines.append(f"• <b>{subj}</b> — <code>{curr_str}</code>{delta_text}")

            text = "\n".join(lines)

            # Сохраняем свежие баллы в базу для следующего сравнения
            if uid in users:
                users[uid]["last_rating"] = grades_dict
                save_json(USERS_FILE, users)
        else:
            text = str(rating_data)

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Обновить", callback_data="portal_rating")],
            [InlineKeyboardButton("✖️ Закрыть рейтинг", callback_data="close_rating")]
        ])

        await status_msg.edit_text(text, parse_mode="HTML", reply_markup=kb)
        return

    elif data == "close_rating":
        try:
            await query.message.delete()
        except Exception:
            pass
        return
    elif data == "profile_change_photo":
        context.user_data["waiting_for_date"] = False
        context.user_data["waiting_for_feedback"] = False
        context.user_data["waiting_for_avatar"] = True
        context.user_data["menu_message_id"] = query.message.message_id

        text = (
            "📸 **Смена фото профиля**\n\n"
            "Пришлите мне фотографию следующим сообщением, и она будет установлена в вашем личном кабинете!"
        )
        with open(BANNERS["main"], "rb") as photo:
            await query.edit_message_media(
                media=InputMediaPhoto(media=photo, caption=text, parse_mode="Markdown"),
                reply_markup=SCHEDULE_KEYBOARD,
            )
        return
    elif data == "main_menu":
        context.user_data["waiting_for_avatar"] = False
        context.user_data["waiting_for_date"] = False
        context.user_data["waiting_for_feedback"] = False
        user_name = query.from_user.first_name
        user_group = context.user_data.get("user_group") or DEFAULT_GROUP
        text = get_welcome_text(user_name, subgroup, group=user_group)
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

        # Сохраняем подгруппу в базу пользователей (для личного кабинета)
        users = load_json(USERS_FILE)
        uid = str(chat_id)
        if uid in users:
            users[uid]["subgroup"] = val
            save_json(USERS_FILE, users)

        # Сохраняем в уведомления
        notifs = load_notifications()
        if uid in notifs:
            notifs[uid]["subgroup"] = val
            save_notifications(notifs)

        user_name = query.from_user.first_name
        current_group = context.user_data.get("user_group") or DEFAULT_GROUP
        text = (
            f"✅ **Подгруппа {val} успешно установлена!**\n\n"
            f"{get_welcome_text(user_name, subgroup, group=current_group)}"
        )
        reply_markup = get_main_keyboard(subgroup)
        banner_key = "main"

    # 6. Обратная связь
    elif data == "feedback":
        context.user_data["waiting_for_date"] = False
        context.user_data["waiting_for_feedback"] = False
        context.user_data["waiting_for_avatar"] = False

        active_t = get_active_ticket(chat_id)
        if active_t:
            last_msg = active_t["messages"][-1]
            sender_label = "Администратор" if last_msg["sender"] == "admin" else "Вы"
            text = (
                f"🎫 **ОБРАЩЕНИЕ #{active_t['id']} (В обработке)**\n"
                f"────────────────────\n"
                f"Создано: *{active_t['created_at']}*\n"
                f"Последнее сообщение ({sender_label}):\n"
                f"> {last_msg['text']}\n\n"
                "Вы можете отправить дополнительное сообщение или закрыть обращение, если вопрос решён."
            )
            reply_markup = get_support_keyboard(has_active_ticket=True)
        else:
            text = (
                "🆘 **СЛУЖБА ПОДДЕРЖКИ**\n"
                "────────────────────\n"
                "Нашли ошибку в расписании, кабинете или преподавателе?\n\n"
                "Нажмите кнопку ниже, чтобы создать обращение. Администратор увидит его и даст ответ."
            )
            reply_markup = get_support_keyboard(has_active_ticket=False)

        with open(BANNERS["main"], "rb") as photo:
            await query.edit_message_media(
                media=InputMediaPhoto(media=photo, caption=text, parse_mode="Markdown"),
                reply_markup=reply_markup,
            )
        return

    elif data in ("ticket_create", "ticket_add_msg"):
        context.user_data["waiting_for_feedback"] = True
        context.user_data["waiting_for_date"] = False
        context.user_data["waiting_for_avatar"] = False
        context.user_data["menu_message_id"] = query.message.message_id

        text = (
            "✍️ **Напишите текст сообщения**\n\n"
            "Отправьте сообщение боту следующим текстом. Оно автоматически прикрепится к вашему обращению."
        )
        cancel_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("◀️ Отмена", callback_data="feedback")]
        ])
        with open(BANNERS["main"], "rb") as photo:
            await query.edit_message_media(
                media=InputMediaPhoto(media=photo, caption=text, parse_mode="Markdown"),
                reply_markup=cancel_kb,
            )
        return

    elif data == "ticket_close_self":
        active_t = get_active_ticket(chat_id)
        if active_t:
            close_ticket_by_id(active_t["id"])

        text = "✅ **Ваше обращение закрыто.**\n\nВы всегда можете посмотреть его в разделе «Мои обращения»."
        reply_markup = get_support_keyboard(has_active_ticket=False)
        with open(BANNERS["main"], "rb") as photo:
            await query.edit_message_media(
                media=InputMediaPhoto(media=photo, caption=text, parse_mode="Markdown"),
                reply_markup=reply_markup,
            )
        return

    elif data == "ticket_history":
        text = (
            "📁 **МОИ ОБРАЩЕНИЯ**\n"
            "────────────────────\n"
            "Выберите тикет для просмотра истории переписки:"
        )
        reply_markup = get_user_tickets_keyboard(chat_id)
        with open(BANNERS["main"], "rb") as photo:
            await query.edit_message_media(
                media=InputMediaPhoto(media=photo, caption=text, parse_mode="Markdown"),
                reply_markup=reply_markup,
            )
        return

    elif data.startswith("ticket_view_"):
        t_id = data.replace("ticket_view_", "")
        tickets = load_tickets()
        ticket = tickets.get(t_id)

        if not ticket:
            text = "⚠️ Обращение не найдено."
            reply_markup = get_user_tickets_keyboard(chat_id)
        else:
            status = "В обработке" if ticket.get("status") == "open" else "Закрыто"
            text_lines = [
                f"🎫 **Обращение #{ticket['id']}** ({status})",
                f"Дата: *{ticket['created_at']}*",
                "────────────────────"
            ]
            for msg in ticket.get("messages", []):
                role = "👤 Вы" if msg["sender"] == "user" else "🛡 Поддержка"
                text_lines.append(f"*{role}* ({msg.get('time', '')}):\n{msg['text']}\n")

            text = "\n".join(text_lines)
            kb = [[InlineKeyboardButton("◀️ К списку обращений", callback_data="ticket_history")]]
            reply_markup = InlineKeyboardMarkup(kb)

        with open(BANNERS["main"], "rb") as photo:
            await query.edit_message_media(
                media=InputMediaPhoto(media=photo, caption=text, parse_mode="Markdown"),
                reply_markup=reply_markup,
            )
        return
    elif data.startswith("adm_reply_"):
        if chat_id not in ADMIN_IDS:
            await query.answer("У вас нет прав администратора.", show_alert=True)
            return
        ticket_id = data.replace("adm_reply_", "")
        context.user_data["admin_replying_ticket"] = ticket_id
        await query.answer()
        await query.message.reply_text(
            f"✍️ Напишите ответ на тикет #{ticket_id} следующим сообщением:"
        )
        return

    elif data.startswith("adm_close_"):
        if chat_id not in ADMIN_IDS:
            await query.answer("У вас нет прав администратора.", show_alert=True)
            return
        ticket_id = data.replace("adm_close_", "")
        if close_ticket_by_id(ticket_id):
            await query.answer("Тикет закрыт!", show_alert=False)
            try:
                await query.edit_message_text(f"🔒 Тикет #{ticket_id} успешно закрыт.")
            except Exception:
                pass
        else:
            await query.answer("Тикет уже был закрыт ранее.", show_alert=True)
        return
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

    elif data == "onboarding_set_group":
        await query.answer()
        context.user_data["waiting_for_group"] = True
        prompt_msg = await context.bot.send_message(
            chat_id=chat_id,
            text="✍️ *Введи номер своей группы:*\n_(Например: `ПИЭ-26-2` или `АИС-26-1`)_",
            parse_mode="Markdown"
        )
        context.user_data["group_prompt_msg_id"] = prompt_msg.message_id
        return

    # 8. Расписание
    else:
        context.user_data["waiting_for_date"] = False
        context.user_data["waiting_for_feedback"] = False
        user_group = context.user_data.get("user_group") or DEFAULT_GROUP
        if data == "today":
            text = get_schedule_for_date(user_group, today, subgroup=subgroup)
            banner_key = "today"
            reply_markup = SCHEDULE_KEYBOARD

        elif data == "tomorrow":
            target = today + timedelta(days=1)
            text = get_schedule_for_date(user_group, target, subgroup=subgroup)
            banner_key = "tomorrow"
            reply_markup = SCHEDULE_KEYBOARD

        elif data in ("week_current", "week"):
            banner_key = "week"
            text = "🗓 **Расписание на текущую неделю сформировано отдельным сообщением ниже 👇**"
            reply_markup = SCHEDULE_KEYBOARD
            full_week_text = get_schedule_for_week(user_group, next_week=False, subgroup=subgroup)
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

# --- ОБРАБОТКА ТЕКСТОВЫХ СООБЩЕНИЙ ---
async def handle_user_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    user = update.effective_user
    text = update.message.text.strip()
    # 0. Ввод группы (онбординг или смена группы)
    if context.user_data.get("waiting_for_group"):
        # 1. Удаляем сообщение пользователя с названием группы
        try:
            await update.message.delete()
        except Exception:
            pass

        # 2. Удаляем сообщение бота "Введи номер своей группы"
        prompt_id = context.user_data.pop("group_prompt_msg_id", None)
        if prompt_id:
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=prompt_id)
            except Exception:
                pass

        entered_group = text.upper().strip()

        # 3. Проверка через парсер
        if not check_group_exists(entered_group):
            err_msg = await context.bot.send_message(
                chat_id=chat_id,
                text=f"⚠️ Группа *{entered_group}* не найдена в расписании УрГЭУ!\n\nПопробуй ещё раз:",
                parse_mode="Markdown"
            )
            context.user_data["group_prompt_msg_id"] = err_msg.message_id
            return

        # 4. Сохраняем группу
        context.user_data["user_group"] = entered_group
        # Сохраняем группу в users.json
        users = load_json(USERS_FILE)
        uid_str = str(chat_id)
        if uid_str not in users:
            users[uid_str] = {}
        users[uid_str]["user_group"] = entered_group
        users[uid_str].pop("subgroup", None)
        save_json(USERS_FILE, users)
        context.user_data["waiting_for_group"] = False

        # 5. Очищаем старую подгруппу (у новой группы своя нумерация)
        context.user_data.pop("subgroup", None)

        # 6. Обновляем экран онбординга до главного меню
        menu_msg_id = context.user_data.get("main_menu_msg_id")
        user_name = update.effective_user.first_name
        welcome_text = get_welcome_text(user_name, subgroup=None, group=entered_group)

        try:
            with open(BANNERS["main"], "rb") as photo:
                await context.bot.edit_message_media(
                    chat_id=chat_id,
                    message_id=menu_msg_id,
                    media=InputMediaPhoto(media=photo, caption=welcome_text, parse_mode="Markdown"),
                    reply_markup=get_main_keyboard(None)
                )
        except Exception:
            with open(BANNERS["main"], "rb") as photo:
                sent_msg = await context.bot.send_photo(
                    chat_id=chat_id,
                    photo=photo,
                    caption=welcome_text,
                    parse_mode="Markdown",
                    reply_markup=get_main_keyboard(None)
                )
            context.user_data["main_menu_msg_id"] = sent_msg.message_id
        return
    # 1. Если админ отвечает на тикет
    if context.user_data.get("admin_replying_ticket"):
        target_ticket_id = context.user_data["admin_replying_ticket"]
        tickets = load_tickets()

        try:
            await update.message.delete()
        except Exception:
            pass

        if target_ticket_id in tickets and tickets[target_ticket_id]["status"] == "open":
            now_str = datetime.now().strftime("%d.%m %H:%M")
            tickets[target_ticket_id]["messages"].append({
                "sender": "admin",
                "text": text,
                "time": now_str
            })
            save_tickets(tickets)
            context.user_data["admin_replying_ticket"] = None

            target_user_id = tickets[target_ticket_id]["user_id"]
            await update.message.reply_text(f"✅ Ответ отправлен в тикет #{target_ticket_id}!")

            # Уведомляем пользователя через редактирование меню или тихое обновление
            try:
                prof_text = (
                    f"🔔 **Поступил ответ по обращению #{target_ticket_id}!**\n"
                    f"────────────────────\n"
                    f"Ответ администратора:\n> {text}\n\n"
                    f"Вы можете посмотреть историю в разделе «Поддержка»."
                )
                with open(BANNERS["main"], "rb") as photo:
                    await context.bot.send_photo(
                        chat_id=target_user_id,
                        photo=photo,
                        caption=prof_text,
                        parse_mode="Markdown",
                        reply_markup=get_support_keyboard(has_active_ticket=True)
                    )
            except Exception:
                pass
        return

    # 2. Если пользователь вводит сообщение тикета
        # Ввод логина от портала УрГЭУ
    if context.user_data.get("waiting_for_portal_login"):
        login = update.message.text.strip()
        context.user_data["temp_portal_login"] = login
        context.user_data["waiting_for_portal_login"] = False
        context.user_data["waiting_for_portal_password"] = True

        # 1. Удаляем текстовое сообщение самого пользователя с логином
        try:
            await update.message.delete()
        except Exception:
            pass

        # 2. Удаляем карточку с вопросом "Введи свой ID пользователя"
        prompt_id = context.user_data.pop("portal_prompt_msg_id", None)
        if prompt_id:
            try:
                await context.bot.delete_message(
                    chat_id=update.effective_chat.id,
                    message_id=prompt_id
                )
            except Exception:
                pass

        # 3. Отправляем запрос пароля и сохраняем его ID
        pwd_msg = await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"Логин `{login}` принят.\n\nТеперь введи *пароль* от портала:\n_(Сообщение с паролем будет сразу же удалено в целях безопасности)_",
            parse_mode="Markdown"
        )
        context.user_data["portal_pwd_prompt_id"] = pwd_msg.message_id
        return
        # Ввод пароля от портала УрГЭУ
    if context.user_data.get("waiting_for_portal_password"):
            password = update.message.text.strip()
            login = context.user_data.get("temp_portal_login")
            chat_id = update.effective_chat.id

            # Удаляем сообщение с паролем из чата
            try:
                await update.message.delete()
            except Exception:
                pass

            # Удаляем вопрос бота "Теперь введи пароль"
            pwd_prompt_id = context.user_data.pop("portal_pwd_prompt_id", None)
            if pwd_prompt_id:
                try:
                    await context.bot.delete_message(chat_id=chat_id, message_id=pwd_prompt_id)
                except Exception:
                    pass

            check_msg = await context.bot.send_message(chat_id=chat_id, text="⏳ Проверяем данные на портале УрГЭУ...")

            # Проверяем авторизацию через функцию из portal.py
            success, msg = await check_portal_auth(login, password)
            try:
                await check_msg.delete()
            except Exception:
                pass

            if success:
                enc_pw = encrypt_password(password)
                users = load_json(USERS_FILE)
                user_entry = users.setdefault(str(chat_id), {})
                user_entry["portal_login"] = login
                user_entry["portal_password"] = enc_pw
                save_json(USERS_FILE, users)

                # Дублируем данные в оперативную память для моментального отклика
                context.user_data["portal_login"] = login
                context.user_data["portal_password"] = enc_pw
                context.user_data["waiting_for_portal_password"] = False
                context.user_data.pop("temp_portal_login", None)

                ok_kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("📊 Посмотреть рейтинг", callback_data="portal_rating")],
                    [InlineKeyboardButton("👤 В профиль", callback_data="profile_menu")]
                ])
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="✅ *Портал УрГЭУ успешно привязан!*",
                    parse_mode="Markdown",
                    reply_markup=ok_kb
                )
                return
            else:
                fail_kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Попробовать снова", callback_data="portal_bind")],
                    [InlineKeyboardButton("👤 В профиль", callback_data="profile_menu")]
                ])
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"❌ *Ошибка входа:* {msg}",
                    parse_mode="Markdown",
                    reply_markup=fail_kb
                )
                return
            # --- ОБРАБОТКА ДАТЫ ---
    if context.user_data.get("waiting_for_date"):
            try:
                await update.message.delete()
            except Exception:
                pass

            menu_id = context.user_data.get("menu_message_id")

            try:
                today = datetime.now(TIMEZONE)
                parsed_date = datetime.strptime(text, "%d.%m")
                target_date = parsed_date.replace(year=today.year, tzinfo=TIMEZONE)

                subgroup = context.user_data.get("subgroup")
                user_group = context.user_data.get("user_group") or DEFAULT_GROUP
                schedule_text = get_schedule_for_date(user_group, target_date, subgroup=subgroup)
                context.user_data["waiting_for_date"] = False

                if len(schedule_text) > 1020:
                    schedule_text = schedule_text[:1015] + "..."

                banner_img = BANNERS.get("date") or BANNERS["main"]
                with open(banner_img, "rb") as photo:
                    await context.bot.edit_message_media(
                        chat_id=chat_id,
                        message_id=menu_id,
                        media=InputMediaPhoto(media=photo, caption=schedule_text, parse_mode="Markdown"),
                        reply_markup=SCHEDULE_KEYBOARD
                    )
            except ValueError:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="⚠️ Неверный формат даты! Введите дату в виде `ДД.ММ` (например, `21.11`):",
                    parse_mode="Markdown"
                )
            except Exception as e:
                print(f"Ошибка при обработке даты: {e}")
            return
    active_t = get_active_ticket(chat_id)
    now_str = datetime.now().strftime("%d.%m %H:%M")

    if active_t:
    # Дополняем существующий открытый тикет
        tickets = load_tickets()
        tickets[active_t["id"]]["messages"].append({
            "sender": "user",
            "text": text,
            "time": now_str
        })
        save_tickets(tickets)
        ticket_id = active_t["id"]
    else:
        # Создаем новый тикет
        new_ticket = create_ticket(chat_id, user.first_name, text)
        ticket_id = new_ticket["id"]

        # Оповещаем администраторов о новом сообщении
        admin_markup = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(f"💬 Ответить #{ticket_id}", callback_data=f"adm_reply_{ticket_id}"),
                InlineKeyboardButton(f"🔒 Закрыть #{ticket_id}", callback_data=f"adm_close_{ticket_id}")
            ]
        ])
        for a_id in ADMIN_IDS:
            try:
                await context.bot.send_message(
                    chat_id=int(a_id),
                    text=(
                        f"🎫 **Новое сообщение в тикете #{ticket_id}**\n"
                        f"От: {user.first_name} (@{user.username or 'нет'})\n"
                        f"Текст:\n{text}"
                    ),
                    reply_markup=admin_markup,
                    parse_mode="Markdown"
                )
                print(f"✅ Уведомление по тикету #{ticket_id} успешно отправлено админу {a_id}")
            except Exception as e:
                print(f"❌ Ошибка отправки админу {a_id}: {e}")

        # Редактируем главное меню у пользователя (чистый экран)
        menu_id = context.user_data.get("menu_message_id") or context.user_data.get("main_menu_msg_id")
        card_text = (
            f"✅ **Сообщение добавлено в обращение #{ticket_id}!**\n"
            f"────────────────────\n"
            f"Администратор уже получил ваше уведомление.\n"
            f"Вы сможете посмотреть ответ прямо в этом меню."
        )
        reply_markup = get_support_keyboard(has_active_ticket=True)

        if menu_id:
            try:
                with open(BANNERS["main"], "rb") as photo:
                    await context.bot.edit_message_media(
                        chat_id=chat_id,
                        message_id=menu_id,
                        media=InputMediaPhoto(media=photo, caption=card_text, parse_mode="Markdown"),
                        reply_markup=reply_markup,
                    )
                return
            except Exception:
                pass

        with open(BANNERS["main"], "rb") as photo:
            await context.bot.send_photo(
                chat_id=chat_id,
                photo=photo,
                caption=card_text,
                parse_mode="Markdown",
                reply_markup=reply_markup
            )
        return

    # Обычная обработка расписания по дате/тексту
    # (если в коде ниже есть ветка для ввода дат или расписания, оставь ее дальше)

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

    async def handle_user_photo(
            update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        chat_id = update.effective_chat.id
        user = update.effective_user

        if not context.user_data.get("waiting_for_avatar"):
            return

        photo = update.message.photo[-1]
        avatar_file_id = photo.file_id

        try:
            await update.message.delete()
        except Exception:
            pass

        users = load_json(USERS_FILE)
        uid = str(user.id)
        if uid not in users:
            track_user_activity(user, "set_avatar")
            users = load_json(USERS_FILE)

        users[uid]["avatar_file_id"] = avatar_file_id
        save_json(USERS_FILE, users)

        context.user_data["waiting_for_avatar"] = False
        menu_id = context.user_data.get("menu_message_id") or context.user_data.get("main_menu_msg_id")

        prof_text, _ = get_profile_data(chat_id, user.first_name)

        if menu_id:
            try:
                await context.bot.edit_message_media(
                    chat_id=chat_id,
                    message_id=menu_id,
                    media=InputMediaPhoto(media=avatar_file_id, caption=prof_text, parse_mode="Markdown"),
                    reply_markup=get_profile_keyboard(False),
                )
                return
            except BadRequest:
                pass

        await update.message.reply_photo(
            photo=avatar_file_id,
            caption=prof_text,
            parse_mode="Markdown",
            reply_markup=get_profile_keyboard(False),
        )

async def handle_user_photo(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    chat_id = update.effective_chat.id
    user = update.effective_user

    if not context.user_data.get("waiting_for_avatar"):
        return

    photo = update.message.photo[-1]
    avatar_file_id = photo.file_id

    try:
        await update.message.delete()
    except Exception:
        pass

    users = load_json(USERS_FILE)
    uid = str(user.id)
    if uid not in users:
        track_user_activity(user, "set_avatar")
        users = load_json(USERS_FILE)

    users[uid]["avatar_file_id"] = avatar_file_id
    save_json(USERS_FILE, users)

    context.user_data["waiting_for_avatar"] = False
    menu_id = context.user_data.get("menu_message_id") or context.user_data.get("main_menu_msg_id")

    prof_text, _ = get_profile_data(chat_id, user.first_name)

    if menu_id:
        try:
            await context.bot.edit_message_media(
                chat_id=chat_id,
                message_id=menu_id,
                media=InputMediaPhoto(media=avatar_file_id, caption=prof_text, parse_mode="Markdown"),
                reply_markup=get_profile_keyboard(False),
            )
            return
        except BadRequest:
            pass

    await update.message.reply_photo(
        photo=avatar_file_id,
        caption=prof_text,
        parse_mode="Markdown",
        reply_markup=get_profile_keyboard(False),
    )
async def on_startup(application):
    # Если на Render поднялся пустой контейнер — восстанавливаем базу
    if not os.path.exists(USERS_FILE) or not load_json(USERS_FILE):
        await restore_data_from_telegram(application.bot)


# ==========================================
# ХАБ УВЕДОМЛЕНИЙ И ДЕДЛАЙНОВ
# ==========================================

async def show_notifications_hub(query, context):
    text = (
        "🔔 <b>Центр управления уведомлениями</b>\n\n"
        "Здесь можно настроить напоминания:\n\n"
        "• <b>Дедлайны (портал)</b> — новые задания, тесты и сроки сдачи.\n"
        "• <b>Уведомления о парах</b> — утренний дайджест и напоминания перед началом."
    )
    keyboard = [
        [InlineKeyboardButton("📝 Дедлайны и тесты", callback_data="deadline_settings")],
        [InlineKeyboardButton("🎓 Напоминания о парах", callback_data="schedule_notify_settings")],
        [InlineKeyboardButton("🔙 В главное меню", callback_data="back_to_main")]
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")


async def show_deadline_settings(query, context, toggle_key=None):
    user_id = str(query.from_user.id)
    users = load_json(USERS_FILE)
    user = users.setdefault(user_id, {})

    settings = user.setdefault("deadline_notify", {
        "new": True,
        "24h": True,
        "3h": True
    })

    if toggle_key and toggle_key in settings:
        settings[toggle_key] = not settings[toggle_key]
        save_json(USERS_FILE, users)

    btn_new = "✅ Новые задания и тесты" if settings.get("new", True) else "❌ Новые задания и тесты"
    btn_24h = "✅ Напоминать за 24 часа" if settings.get("24h", True) else "❌ Напоминать за 24 часа"
    btn_3h = "✅ Напоминать за 3 часа" if settings.get("3h", True) else "❌ Напоминать за 3 часа"

    text = (
        "⚙️ <b>Настройка уведомлений о дедлайнах</b>\n\n"
        "Нажимай на кнопки, чтобы включить/отключить оповещения:\n\n"
        "• <b>Новые задания и тесты</b> — уведомление сразу при появлении на портале.\n"
        "• <b>За 24 часа</b> — предупреждение за сутки до срока сдачи.\n"
        "• <b>За 3 часа</b> — финальное горящее напоминание."
    )

    keyboard = [
        [InlineKeyboardButton(btn_new, callback_data="set_deadline:new")],
        [InlineKeyboardButton(btn_24h, callback_data="set_deadline:24h")],
        [InlineKeyboardButton(btn_3h, callback_data="set_deadline:3h")],
        [InlineKeyboardButton("🔙 Назад в уведомления", callback_data="notif_menu")]
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")


async def show_schedule_notify_settings(query, context, toggle_key=None):
    user_id = str(query.from_user.id)
    users = load_json(USERS_FILE)
    user = users.setdefault(user_id, {})

    settings = user.setdefault("schedule_notify", {
        "morning": True,
        "before_pair": True
    })

    if toggle_key and toggle_key in settings:
        settings[toggle_key] = not settings[toggle_key]
        save_json(USERS_FILE, users)

    btn_morning = "✅ Утренний план (07:30)" if settings.get("morning", True) else "❌ Утренний план (07:30)"
    btn_before = "✅ За 15 минут до пары" if settings.get("before_pair", True) else "❌ За 15 минут до пары"

    text = (
        "🎓 <b>Настройка уведомлений о парах</b>\n\n"
        "Управляй напоминаниями расписания:\n\n"
        "• <b>Утренний план</b> — список пар на сегодня каждое утро.\n"
        "• <b>За 15 минут</b> — предупреждение перед началом пары с аудиторией."
    )

    keyboard = [
        [InlineKeyboardButton(btn_morning, callback_data="set_sched:morning")],
        [InlineKeyboardButton(btn_before, callback_data="set_sched:before_pair")],
        [InlineKeyboardButton("🔙 Назад в уведомления", callback_data="notif_menu")]
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
def main() -> None:
    threading.Thread(target=run_fake_server, daemon=True).start()

    print("Бот запускается...")
    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .post_init(on_startup)
        .build()
    )

    try:
        if app.job_queue:
            app.job_queue.run_repeating(send_daily_notifications, interval=60, first=10)
            app.job_queue.run_repeating(sync_backup_to_telegram, interval=900, first=30)
    except Exception as e:
        print(f"[WARN] Не удалось запустить фоновые задачи: {e}")

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CommandHandler("stats", admin_command))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_user_text))
    app.add_handler(MessageHandler(filters.PHOTO, handle_user_photo))

    print("✅ Бот успешно запущен и ожидает сообщений!")
    app.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    main()