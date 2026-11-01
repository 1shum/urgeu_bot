from db import get_users_cache, save_user_db, init_db, get_notifs_cache, save_notif_db
import json
import os
import asyncio
import re
import threading
import html
import httpx
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

BOT_TOKEN = os.environ["BOT_TOKEN"]
DEFAULT_GROUP = "ПИЭ-26-2"
ADMIN_ID = 1087118817  # здесь твой числовой ID
ADMIN_IDS = [1087118817]
TIMEZONE = ZoneInfo("Asia/Yekaterinburg")

# --- ДИЗАЙН-СИСТЕМА ---------------------------------------------------------
# Палитра под чёрно-красные шапки: у каждого раздела свой знак.
#   📅 расписание  👤 профиль  🔔 уведомления  🌐 портал  💬 поддержка  🔎 выбор даты
#   🔥 дедлайны  🏆 рейтинг  🎓 группа  ⏰ время  🟢 включено / ⚪ выключено
#   ✅ успех  ❌ ошибка  ⚠️ внимание  ✏️ ввод текста  ‹ назад  ↻ обновить  ✕ отмена
DIV = "─" * 20
WEEKDAYS_RU = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]


def md_esc(value) -> str:
    """Экранирует пользовательский текст для parse_mode=Markdown (имена, ники, группы)."""
    return re.sub(r"([_*`\[])", r"\\\1", str(value))
TICKETS_FILE = "tickets.json"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Шапка каждого экрана. Файлы лежат в папке banners/ рядом с bot.py.
BANNERS = {
    "main": "banners/main.png",              # главное меню
    "today": "banners/today.png",            # Сегодня
    "tomorrow": "banners/tomorrow.png",      # Завтра
    "week": "banners/week.png",              # Текущая неделя
    "date": "banners/date.png",              # Выбрать день
    "profile": "banners/profile.png",        # Личный кабинет
    "notifications": "banners/notif.png",    # Уведомления
    "portal": "banners/portal.png",          # Портал УрГЭУ
    "feedback": "banners/feedback.png",      # Поддержка / Идеи
    "subgroup": "banners/main.png",          # отдельной шапки нет — используем главную
}
BANNERS = {k: os.path.join(BASE_DIR, v) for k, v in BANNERS.items()}
for _key, _path in BANNERS.items():
    if not os.path.exists(_path):
        print(f"[WARN] Шапка '{_key}' не найдена: {_path}")

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

def load_notifications() -> dict:
    return get_notifs_cache()

async def save_notification_for_user(user_id: str, notif_data: dict):
    await save_notif_db(user_id, notif_data)

async def track_user_activity(user, action_name: str) -> None:
    """Фиксирует пользователя и его активность в Neon."""
    if not user:
        return
    users = get_users_cache()
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

    await save_user_db(uid, users[uid])

# --- КЛАВИАТУРЫ ---
def get_main_keyboard(subgroup: int = None) -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(text="📅 Сегодня", callback_data="today"),
            InlineKeyboardButton(text="🗓 Завтра", callback_data="tomorrow"),
        ],
        [
            InlineKeyboardButton(text="📋 Текущая неделя", callback_data="week_current"),
            InlineKeyboardButton(text="🔎 Выбрать день", callback_data="pick_date"),
        ],
        [
            InlineKeyboardButton(text="👤 Личный кабинет", callback_data="profile_menu"),
            InlineKeyboardButton(text="🔔 Уведомления", callback_data="notif_menu"),
        ],
        [
            # Добавляем кнопку портала отдельным рядом
            InlineKeyboardButton(text="🌐 Портал УрГЭУ", callback_data="portal_menu")
        ]
    ]

    if not subgroup:
        buttons.append([
            InlineKeyboardButton(text="➕ Добавить подгруппу", callback_data="choose_subgroup")
        ])

    buttons.append([
        InlineKeyboardButton(text="💬 Поддержка / Идеи", callback_data="feedback")
    ])

    return InlineKeyboardMarkup(buttons)


def get_portal_keyboard(is_linked: bool) -> InlineKeyboardMarkup:
    buttons = []

    if not is_linked:
        buttons.append([InlineKeyboardButton("🔗 Привязать портал", callback_data="portal_bind")])
    else:
        buttons.append([InlineKeyboardButton("🔥 Мои дедлайны", callback_data="show_deadlines_list")])
        buttons.append([InlineKeyboardButton("🏆 Мой рейтинг", callback_data="portal_rating")])
        buttons.append([InlineKeyboardButton("🔓 Отвязать портал", callback_data="portal_unbind")])

    buttons.append([InlineKeyboardButton("‹ Главное меню", callback_data="main_menu")])

    return InlineKeyboardMarkup(buttons)


def get_notif_keyboard(current_time: str = None) -> InlineKeyboardMarkup:
    times = ["19:00", "20:00", "21:00"]
    buttons = []

    row = []
    for t in times:
        mark = "✅ " if t == current_time else ""
        row.append(InlineKeyboardButton(f"{mark}{t}", callback_data=f"set_notif_{t}"))
    buttons.append(row)

    buttons.append([InlineKeyboardButton("✏️ Своё время", callback_data="set_notif_custom")])
    buttons.append([InlineKeyboardButton("🔕 Выключить", callback_data="set_notif_off")])

    return InlineKeyboardMarkup(buttons)

def get_profile_keyboard(is_portal_linked: bool = False) -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton("📸 Сменить фото", callback_data="profile_change_photo")
        ],
        [
            InlineKeyboardButton("↻ Сменить группу", callback_data="unlink_group")
        ],
        [
            InlineKeyboardButton("👥 Подгруппа", callback_data="choose_subgroup"),
            InlineKeyboardButton("🔔 Уведомления", callback_data="notif_menu")
        ]
    ]

    buttons.append([
        InlineKeyboardButton("‹ Главное меню", callback_data="main_menu")
    ])
    return InlineKeyboardMarkup(buttons)


def get_support_keyboard(has_active_ticket: bool) -> InlineKeyboardMarkup:
    buttons = []
    if not has_active_ticket:
        buttons.append([InlineKeyboardButton("✏️ Написать обращение", callback_data="ticket_create")])
    else:
        buttons.append([InlineKeyboardButton("➕ Дополнить обращение", callback_data="ticket_add_msg")])
        buttons.append([InlineKeyboardButton("🔒 Закрыть обращение", callback_data="ticket_close_self")])

    buttons.append([InlineKeyboardButton("📁 Мои обращения", callback_data="ticket_history")])
    buttons.append([InlineKeyboardButton("‹ Главное меню", callback_data="main_menu")])
    return InlineKeyboardMarkup(buttons)


def get_user_tickets_keyboard(user_id: int) -> InlineKeyboardMarkup:
    tickets = load_tickets()
    buttons = []
    user_tickets = [t for t in tickets.values() if t.get("user_id") == user_id]

    # Показываем последние 5 обращений
    for t in sorted(user_tickets, key=lambda x: int(x["id"]), reverse=True)[:5]:
        status_icon = "🟢" if t.get("status") == "open" else "⚪"
        btn_text = f"{status_icon} Тикет #{t['id']} от {t['created_at']}"
        buttons.append([InlineKeyboardButton(btn_text, callback_data=f"ticket_view_{t['id']}")])

    buttons.append([InlineKeyboardButton("‹ Назад в поддержку", callback_data="feedback")])
    return InlineKeyboardMarkup(buttons)

SUBGROUP_KEYBOARD = InlineKeyboardMarkup([
    [
        InlineKeyboardButton("1️⃣ Подгруппа 1", callback_data="set_subgroup_1"),
        InlineKeyboardButton("2️⃣ Подгруппа 2", callback_data="set_subgroup_2"),
    ],
    [
        InlineKeyboardButton("‹ Главное меню", callback_data="main_menu"),
    ]
])

SCHEDULE_KEYBOARD = InlineKeyboardMarkup([
    [
        InlineKeyboardButton("‹ Главное меню", callback_data="main_menu"),
    ]
])

WEEK_KEYBOARD = InlineKeyboardMarkup([
    [
        InlineKeyboardButton("‹ Назад в главное меню", callback_data="close_week"),
    ]
])

def get_profile_data(user_id: int, first_name: str, user_group: str = "Не выбрана") -> tuple[str, str | None]:
    """Возвращает готовый текст профиля и file_id аватарки (если есть)."""
    users = get_users_cache()
    notifs = load_notifications()

    u_info = users.get(str(user_id), {})
    n_info = notifs.get(str(user_id), {})

    group_str = f"`{user_group}`" if user_group else "не выбрана"
    subgroup = n_info.get("subgroup") or u_info.get("subgroup")
    subgroup_str = f"подгруппа {subgroup}" if subgroup else "подгруппа не выбрана"

    is_notif = n_info.get("enabled", False)
    notif_time = n_info.get("time") or "—"
    notif_str = f"🟢 {notif_time}" if is_notif else "⚪ выключены"

    portal_str = "🟢 подключён" if u_info.get("portal_login") else "⚪ не подключён"

    actions = u_info.get("actions_count", 1)
    username = f"  ·  @{md_esc(u_info.get('username', '').lstrip('@'))}" if u_info.get("username") else ""

    return (
        f"👤 *{md_esc(first_name)}*{username}\n"
        f"🎓 Группа  {group_str}  ·  {subgroup_str}\n\n"
        f"🔔 Уведомления  —  {notif_str}\n"
        f"🌐 Портал УрГЭУ  —  {portal_str}\n"
        f"⚡ Действий в боте  —  `{actions}`\n\n"
        f"📸 _Отправьте фото в чат, чтобы сменить аватар_"
    ), u_info.get("avatar_file_id")

def get_welcome_text(user_name: str, subgroup: int = None, group: str = DEFAULT_GROUP) -> str:
    now = datetime.now(TIMEZONE)
    subgroup_str = f"  ·  подгруппа {subgroup}" if subgroup else ""
    return (
        f"👋 Привет, *{md_esc(user_name)}*!\n\n"
        f"🎓 Группа  `{group}`{subgroup_str}\n"
        f"📅 Сегодня  {WEEKDAYS_RU[now.weekday()]}, {now:%d.%m}\n\n"
        f"Выберите раздел в меню ниже 👇"
    )

# --- ФОНОВАЯ ОТПРАВКА УВЕДОМЛЕНИЙ ---
async def send_daily_notifications(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Проверяет пользователей каждую минуту и присылает пуш в заданное время."""
    now = datetime.now(TIMEZONE)
    current_time_str = now.strftime("%H:%M")

    # --- ДОБАВЬ ВОТ ЭТИ ДВЕ СТРОЧКИ ---
    print(f"[NOTIF DEBUG] Проверка времени: {current_time_str}")
    # -----------------------------------

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
                ],
                [
                    InlineKeyboardButton(
                        "🙈 Скрыть",
                        callback_data="close_daily_notif"
                    )
                ]
            ])

            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=short_text,
                    parse_mode='Markdown',
                    reply_markup=keyboard,
                )
            except Exception as e:
                print(f"Ошибка отправки уведомления пользователю {chat_id}: {e}")

# --- КОМАНДЫ ---
async def start_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    await track_user_activity(update.effective_user, "/start")

    context.user_data["waiting_for_date"] = False
    context.user_data["waiting_for_feedback"] = False
    context.user_data["waiting_for_group"] = False

    user_name = update.effective_user.first_name
    chat_id = update.effective_chat.id

    # 1. Если группа ещё не выбрана — показываем экран онбординга (только 1 кнопка)
    users = get_users_cache()
    u_info = users.get(str(chat_id), {})

    # Восстанавливаем сессию из базы данных
    user_group = u_info.get("user_group") or context.user_data.get("user_group")
    if user_group:
        context.user_data["user_group"] = user_group

    if u_info.get("subgroup"):
        context.user_data["subgroup"] = u_info["subgroup"]

    if u_info.get("portal_login"):
        context.user_data["portal_login"] = u_info["portal_login"]
        context.user_data["portal_password"] = u_info.get("portal_password")
    if not user_group:
        temp_msg = await update.message.reply_text("⏳", reply_markup=ReplyKeyboardRemove())
        await temp_msg.delete()

        welcome_new = (
            f"👋 Привет, *{md_esc(user_name)}*!\n\n"
            f"Я помогаю студентам *УрГЭУ*:\n"
            f"📅 расписание пар\n"
            f"🔥 дедлайны с портала\n"
            f"🏆 рейтинг по предметам\n\n"
            f"Чтобы начать, укажите номер своей учебной группы 👇"
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

    users = get_users_cache()
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
        "📊 *СТАТИСТИКА БОТА*",
        "",
        f"👥 Пользователей: *{total_users}*",
        f"🔔 С уведомлениями: *{active_notifs}*",
        f"⚡ Всего действий: *{total_actions}*",
        "",
        "*Последние активные:*"
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


async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда для рассылки всем пользователям бота (только для админа)"""
    if update.effective_user.id != ADMIN_ID:
        return

    # Берем текст, который ты напишешь после команды /broadcast
    text_to_send = update.message.text.replace("/broadcast", "").strip()

    if not text_to_send:
        await update.message.reply_text("❌ Использование: `/broadcast <текст>`", parse_mode="Markdown")
        return

    users = get_users_cache()
    await update.message.reply_text(f"⏳ Начинаю рассылку для {len(users)} пользователей...")

    success_count = 0
    error_count = 0

    for user_id in users.keys():
        try:
            broadcast_kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🙈 Скрыть", callback_data="close_broadcast")]
            ])

            for user_id in users.keys():
                try:
                    await context.bot.send_message(
                        chat_id=user_id,
                        text=text_to_send,
                        parse_mode="Markdown",
                        reply_markup=broadcast_kb
                    )
                    success_count += 1
                    await asyncio.sleep(0.05)
                except Exception as e:
                    error_count += 1
                    print(f"Ошибка отправки пользователю {user_id}: {e}")
            success_count += 1
            import asyncio
            await asyncio.sleep(0.05)
        except Exception as e:
            error_count += 1
            print(f"Ошибка отправки пользователю {user_id}: {e}")

    await update.message.reply_text(
        f"✅ *Рассылка завершена!*\n"
        f"Успешно доставлено: {success_count}\n"
        f"Ошибок: {error_count}",
        parse_mode="Markdown"
    )

async def send_rich_html(chat_id: int, html_text: str, reply_markup=None) -> None:
    """Отправка rich-сообщения напрямую через Bot API (sendRichMessage)."""
    payload = {"chat_id": chat_id, "rich_message": {"html": html_text}}
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup.to_dict()
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendRichMessage", json=payload
        )
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(data.get("description", "sendRichMessage failed"))


def build_rating_html(rating_data: list, prev_grades: dict) -> str:
    """Собирает красивую таблицу рейтинга в rich-HTML."""
    rows = []
    values = []
    for item in rating_data:
        subj = html.escape(str(item["name"]))
        curr_val = item["val"]
        curr_str = html.escape(str(item["raw_str"]))

        delta = "—"
        if curr_val is not None:
            values.append(curr_val)
            if item["name"] in prev_grades:
                diff = round(curr_val - prev_grades[item["name"]], 2)
                if diff > 0:
                    delta = f"🟢 ▲ +{diff}%"
                elif diff < 0:
                    delta = f"🔴 ▼ {diff}%"

        rows.append(
            f'<tr><td>{subj}</td>'
            f'<td align="center"><b>{curr_str}</b></td>'
            f'<td align="center">{delta}</td></tr>'
        )

    table = (
        '<table bordered striped>'
        '<tr><th>Предмет</th><th>Баллы</th><th>Динамика</th></tr>'
        + "".join(rows) +
        '</table>'
    )

    summary = ""
    if values:
        avg = round(sum(values) / len(values), 1)
        summary = f"<p>📊 Средний результат: <b>{avg}%</b></p>"

    now = datetime.now(ZoneInfo("Asia/Yekaterinburg")).strftime("%d.%m.%Y %H:%M")
    return (
        "<h3>🏆 Академический рейтинг</h3>"
        f"{table}{summary}"
        f"<footer>Обновлено: {now}</footer>"
    )



def _visible_len(t: str) -> int:
    """Длина текста без HTML-тегов (именно её считает Telegram для лимитов)."""
    return len(html.unescape(re.sub(r"<[^>]+>", "", t)))


def _deadline_kind(d: dict) -> tuple[str, str]:
    """Возвращает (иконка, подпись): Тест / Задание / иное."""
    raw = str(d.get("type") or "").strip()
    t = raw.lower()
    if "тест" in t or "quiz" in t:
        return "🎯", "Тест"
    if "задан" in t or "assign" in t:
        return "📝", "Задание"
    if not t and "тест" in str(d.get("title") or "").lower():
        return "🎯", "Тест"
    return "📌", (raw or "Работа")


def _urgency(dt: datetime, now: datetime) -> tuple[str, str]:
    """Цветная метка срочности и текст «осталось»."""
    secs = int((dt - now).total_seconds())
    if secs < 0:
        return "⚫", "просрочено"
    days, rem = divmod(secs, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        left = f"{days}д {hours}ч"
    elif hours:
        left = f"{hours}ч {minutes}м"
    else:
        left = f"{minutes}м"
    if secs < 3 * 3600:
        mark = "🔴"
    elif secs < 24 * 3600:
        mark = "🟠"
    elif secs < 3 * 86400:
        mark = "🟡"
    else:
        mark = "🟢"
    return mark, f"ост. {left}"


def build_deadlines_html(items: list, total_count: int, max_len: int = 3900) -> str:
    """Собирает список дедлайнов в HTML: курс → блок цитаты с работами."""
    now = datetime.now(TIMEZONE).replace(tzinfo=None)

    grouped = {}
    for d in items:
        grouped.setdefault(d.get("course") or "Курс", []).append(d)

    text = f"🔥 <b>Активные дедлайны</b>  ·  {len(items)} из {total_count}\n\n"
    shown = 0
    for course, works in grouped.items():
        lines = []
        for d in works:
            icon, kind = _deadline_kind(d)
            title = html.escape(str(d.get("title") or "Без названия"))

            when = "срок не указан"
            iso = d.get("deadline_iso")
            if iso:
                try:
                    dt = datetime.fromisoformat(iso)
                    if dt.tzinfo:
                        dt = dt.astimezone(TIMEZONE).replace(tzinfo=None)
                    mark, left = _urgency(dt, now)
                    when = f"{mark} {dt:%d.%m %H:%M} · {left}"
                except Exception:
                    pass
            lines.append(f"{icon} <b>{kind}:</b> {title}\n{when}")

        block = (
            f"📚 <b>{html.escape(str(course))}</b>\n"
            f"<blockquote>{chr(10).join(chr(10) + l if i else l for i, l in enumerate(lines))}</blockquote>\n"
        )
        if _visible_len(text + block) > max_len:
            text += f"… и ещё {len(items) - shown} шт. не поместилось"
            return text
        text += block
        shown += len(works)

    legend = "<i>🔴 &lt;3ч · 🟠 &lt;24ч · 🟡 &lt;3 дн · 🟢 позже</i>"
    if _visible_len(text + legend) <= max_len:
        text += legend
    return text


async def swap_banner(query, text, reply_markup, banner_key, parse_mode="Markdown"):
    """Показывает экран с нужной шапкой: меняет и фото, и подпись у текущего сообщения."""
    path = BANNERS.get(banner_key, BANNERS["main"])
    try:
        with open(path, "rb") as photo:
            await query.edit_message_media(
                media=InputMediaPhoto(media=photo, caption=text, parse_mode=parse_mode),
                reply_markup=reply_markup,
            )
    except BadRequest as e:
        if "not modified" in str(e).lower():
            return
        # сообщение нельзя отредактировать (например, оно без фото) — пересоздаём
        try:
            await query.message.delete()
        except Exception:
            pass
        with open(path, "rb") as photo:
            await query.get_bot().send_photo(
                chat_id=query.message.chat_id,
                photo=photo,
                caption=text,
                parse_mode=parse_mode,
                reply_markup=reply_markup,
            )


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

    await track_user_activity(query.from_user, f"btn:{data}")

    # Выгрузка базы данных в чат для администратора
    if data == "admin_download_db":
        if query.from_user.id == ADMIN_ID:
            import io
            users = get_users_cache()
            json_bytes = json.dumps(users, ensure_ascii=False, indent=2).encode("utf-8")
            bio = io.BytesIO(json_bytes)
            bio.name = "users.json"
            await context.bot.send_document(
                chat_id=chat_id,
                document=bio,
                filename="users.json",
                caption=f" Актуальная база пользователей из Neon ({len(users)} чел.)."
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

    if data in ("close_daily_notif", "close_broadcast"):
        try:
            await query.message.delete()
        except Exception:
            pass
        return
    # 3. Меню настроек уведомлений
    # 3. Меню управления всеми уведомлениями (Хаб)
    if data == "notif_menu":
        text = (
        "🔔 <b>Выберите, что настроить:</b>\n\n"
        "<blockquote>"
        "🔥 <b>Дедлайны (портал)</b> — новые тесты, лабы и сроки сдачи\n"
        "⏰ <b>Уведомления о парах</b> — вечерняя рассылка расписания на завтра"
        "</blockquote>"
        )
        buttons = [
            [InlineKeyboardButton("🔥 Дедлайны (портал)", callback_data="deadline_hub")],
            [InlineKeyboardButton("⏰ Уведомления о парах", callback_data="pairs_notif_menu")],
            [InlineKeyboardButton("‹ В главное меню", callback_data="main_menu")]
        ]
        with open(BANNERS["notifications"], "rb") as photo:
            await query.edit_message_media(
                media=InputMediaPhoto(media=photo, caption=text, parse_mode="HTML"),
                reply_markup=InlineKeyboardMarkup(buttons),
            )
        return
    elif data.startswith("set_notif_"):
        val = data.replace("set_notif_", "")
        uid = str(chat_id)

        # Если юзер нажал "Ввести свое время"
        if val == "custom":
            context.user_data["waiting_for_notif_time"] = True
            await query.answer()
            await query.edit_message_caption(
                caption="✏️ Напишите желаемое время (ЧЧ:ММ):",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✕ Отмена", callback_data="notif_menu")]])
            )
            return

        notifs = get_notifs_cache()
        if uid not in notifs:
            notifs[uid] = {}

        if val == "off":
            notifs[uid]["enabled"] = False
            notifs[uid]["time"] = None
            await query.answer("Уведомления выключены")
        else:
            notifs[uid]["enabled"] = True
            notifs[uid]["time"] = val
            await query.answer(f"Время установлено на {val}")

        from db import save_notif_db
        await save_notif_db(uid, notifs[uid])

        # Обновляем меню
        query.data = "notif_menu"
        await handle_callback(update, context)
        return

    elif data == "portal_menu":
        users = get_users_cache()
        uid = str(chat_id)
        user_data = users.get(uid, {})

        # Проверяем, есть ли у пользователя логин и пароль от портала.
        # (Замени ключи "portal_login" и "portal_password", если в базе они названы иначе)
        is_linked = bool(user_data.get("portal_login") and user_data.get("portal_password"))

        status_text = "🟢 привязан" if is_linked else "⚪ не привязан"

        text = (
        f"🔗 Аккаунт  —  {status_text}\n\n"
        "🔥 *Дедлайны* — тесты и задания со сроками\n"
        "🏆 *Рейтинг* — ваши баллы по предметам"
        + ("" if is_linked else "\n\n_Привяжите аккаунт, чтобы смотреть всё это прямо в боте._")
        )

        await swap_banner(query, text, get_portal_keyboard(is_linked), "portal")
        return
    # Подменю настройки пар (твое прежнее меню)
    if data == "pairs_notif_menu":
        notifs = load_notifications()
        user_info = notifs.get(str(chat_id), {})
        is_enabled = user_info.get("enabled", False)
        saved_time = user_info.get("time", "не выбрано")

        status_icon = "🟢 включены" if is_enabled else "⚪ выключены"
        text = (
        "⏰ <b>Уведомления о парах</b>\n\n"
        f"🔔 Статус  —  {status_icon}\n"
        f"🕗 Время  —  <b>{html.escape(str(saved_time or 'не выбрано'))}</b>\n\n"
        "<blockquote>Каждый день бот присылает краткий список пар на завтра.</blockquote>\n"
        "Выберите удобное время 👇"
        )
        keyboard_buttons = get_notif_keyboard(saved_time if is_enabled else None).inline_keyboard
        # Добавляем кнопку возврата в хаб уведомлений
        new_kb = list(keyboard_buttons) + [[InlineKeyboardButton("‹ Назад в уведомления", callback_data="notif_menu")]]

        with open(BANNERS["notifications"], "rb") as photo:
            await query.edit_message_media(
                media=InputMediaPhoto(media=photo, caption=text, parse_mode="HTML"),
                reply_markup=InlineKeyboardMarkup(new_kb),
            )
        return
        # Подменю дедлайнов портала
    if data == "deadline_hub":
            users = get_users_cache()
            user = users.setdefault(str(chat_id), {})
            settings = user.setdefault("deadline_notify", {"new": True, "24h": True, "3h": True})

            btn_new = "🟢 Новые работы" if settings.get("new", True) else "⚪ Новые работы"
            btn_24h = "🟢 За 24 часа" if settings.get("24h", True) else "⚪ За 24 часа"
            btn_3h = "🟢 За 3 часа" if settings.get("3h", True) else "⚪ За 3 часа"

            text = (
            "🔥 <b>Уведомления о дедлайнах</b>\n\n"
            "Напоминания о тестах и заданиях с портала УрГЭУ:\n"
            "<blockquote>"
            "🆕 <b>Новые работы</b> — когда преподаватель выложил тест или задание\n"
            "⏰ <b>За 24 часа</b> — напоминание за сутки до срока\n"
            "🔥 <b>За 3 часа</b> — срочное предупреждение"
            "</blockquote>"
            )
            buttons = [
                [InlineKeyboardButton(btn_new, callback_data="toggle_dl:new"),
                 InlineKeyboardButton(btn_24h, callback_data="toggle_dl:24h")],
                [InlineKeyboardButton(btn_3h, callback_data="toggle_dl:3h")],
                [InlineKeyboardButton("‹ Назад в уведомления", callback_data="notif_menu")]
            ]
            with open(BANNERS["notifications"], "rb") as photo:
                await query.edit_message_media(
                    media=InputMediaPhoto(media=photo, caption=text, parse_mode="HTML"),
                    reply_markup=InlineKeyboardMarkup(buttons),
                )
            return

        # Переключение тумблеров дедлайнов
    if data.startswith("toggle_dl:"):
            key = data.split(":")[1]
            users = get_users_cache()
            user = users.setdefault(str(chat_id), {})
            settings = user.setdefault("deadline_notify", {"new": True, "24h": True, "3h": True})
            if key in settings:
                settings[key] = not settings[key]
                await save_user_db(str(chat_id), users[str(chat_id)])

            btn_new = "🟢 Новые работы" if settings.get("new", True) else "⚪ Новые работы"
            btn_24h = "🟢 За 24 часа" if settings.get("24h", True) else "⚪ За 24 часа"
            btn_3h = "🟢 За 3 часа" if settings.get("3h", True) else "⚪ За 3 часа"

            text = (
            "🔥 <b>Уведомления о дедлайнах</b>\n\n"
            "Напоминания о тестах и заданиях с портала УрГЭУ:\n"
            "<blockquote>"
            "🆕 <b>Новые работы</b> — когда преподаватель выложил тест или задание\n"
            "⏰ <b>За 24 часа</b> — напоминание за сутки до срока\n"
            "🔥 <b>За 3 часа</b> — срочное предупреждение"
            "</blockquote>"
            )
            buttons = [
                [InlineKeyboardButton(btn_new, callback_data="toggle_dl:new"),
                 InlineKeyboardButton(btn_24h, callback_data="toggle_dl:24h")],
                [InlineKeyboardButton(btn_3h, callback_data="toggle_dl:3h")],
                [InlineKeyboardButton("‹ Назад в уведомления", callback_data="notif_menu")]
            ]
            with open(BANNERS["notifications"], "rb") as photo:
                await query.edit_message_media(
                    media=InputMediaPhoto(media=photo, caption=text, parse_mode="HTML"),
                    reply_markup=InlineKeyboardMarkup(buttons),
                )
            return

        # Просмотр списка актуальных дедлайнов прямо из бота
    # Просмотр списка актуальных дедлайнов прямо из бота
    # Просмотр списка актуальных дедлайнов
    # Просмотр списка актуальных дедлайнов
    if data in ("show_deadlines_list", "show_all_deadlines"):
        # 1. Достаем учетные данные пользователя из users.json
        users = get_users_cache()
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
                "⚠️ *Портал не привязан!*\n\n"
                "Чтобы просматривать задания и тесты, перейди в *Личный кабинет* и привяжи аккаунт портала УрГЭУ."
            )
            buttons = [
                [InlineKeyboardButton("🔗 Привязать портал", callback_data="portal_bind")],
                [InlineKeyboardButton("‹ Назад в портал", callback_data="portal_menu")]
            ]
            with open(BANNERS["portal"], "rb") as photo:
                await query.edit_message_media(
                    media=InputMediaPhoto(media=photo, caption=warn_text, parse_mode="Markdown"),
                    reply_markup=InlineKeyboardMarkup(buttons),
                )
            return

        # Показываем плашку загрузки
        loading_text = (
            "⏳ *Сканирую портал УрГЭУ...*\n\n"
            "Проверяю 12 учебных курсов на наличие тестов и заданий.\n"
            "Пожалуйста, подожди несколько секунд..."
        )
        try:
            with open(BANNERS["portal"], "rb") as photo:
                await query.edit_message_media(
                    media=InputMediaPhoto(media=photo, caption=loading_text, parse_mode="Markdown"),
                    reply_markup=InlineKeyboardMarkup(
                        [[InlineKeyboardButton("⏳ Загрузка…", callback_data="none")]])
                )
        except Exception as e:
            print(f"[ERROR] Не удалось показать экран загрузки: {e}")

        # 3. Запрашиваем данные с портала
        try:
            deadlines = await fetch_portal_deadlines(login, raw_password)

            if not deadlines:
                text = (
                    "🎉 <b>Горящих дедлайнов нет!</b>\n\n"
                    "Все тесты и задания сданы или преподаватели ещё не выложили новые работы."
                )
                buttons = [
                    [InlineKeyboardButton("↻ Обновить", callback_data="show_deadlines_list")],
                    [InlineKeyboardButton("‹ Назад в портал", callback_data="portal_menu")]
                ]
            else:
                show_all = (data == "show_all_deadlines")

                # Сортируем дедлайны по времени (самые горящие первыми)
                deadlines.sort(key=lambda x: x.get("timestamp", 0))

                total_count = len(deadlines)
                limit = 7 if not show_all else total_count
                visible_deadlines = deadlines[:limit]

                text = build_deadlines_html(visible_deadlines, total_count)

                # Клавиатура с кнопкой разворота
                buttons = []
                if total_count > 7:
                    if not show_all:
                        remaining = total_count - 7
                        buttons.append([InlineKeyboardButton(f"📂 Показать остальные (ещё {remaining})",
                                                             callback_data="show_all_deadlines")])
                    else:
                        buttons.append([InlineKeyboardButton("🔼 Свернуть до топ-7", callback_data="show_deadlines_list")])

                buttons.append([
                    InlineKeyboardButton("↻ Обновить", callback_data="show_deadlines_list"),
                    InlineKeyboardButton("‹ Назад в портал", callback_data="portal_menu")
                ])

        except Exception as err:
            print(f"[ERROR] Ошибка сбора дедлайнов: {err}")
            text = f"❌ Ошибка при обращении к порталу:\n{html.escape(str(err))}"
            buttons = [
                [InlineKeyboardButton("↻ Попробовать снова", callback_data="show_deadlines_list")],
                [InlineKeyboardButton("‹ Назад в портал", callback_data="portal_menu")]
            ]

        # Подпись к фото ограничена 1024 символами: длинный список шлём обычным сообщением
        if _visible_len(text) > 1000:
            try:
                await query.message.delete()
            except Exception:
                pass
            await context.bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(buttons)
            )
        else:
            with open(BANNERS["portal"], "rb") as photo:
                await query.edit_message_media(
                    media=InputMediaPhoto(media=photo, caption=text, parse_mode="HTML"),
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
        users = get_users_cache()
        user_entry = users.get(str(chat_id), {})

        # Восстанавливаем данные из базы в сессию
        user_group = user_entry.get("user_group") or context.user_data.get("user_group", "Не выбрана")
        context.user_data["user_group"] = user_group
        if user_entry.get("subgroup"):
            context.user_data["subgroup"] = user_entry["subgroup"]
        if user_entry.get("portal_login"):
            context.user_data["portal_login"] = user_entry["portal_login"]
            context.user_data["portal_password"] = user_entry.get("portal_password")

        prof_text, avatar_id = get_profile_data(chat_id, query.from_user.first_name, user_group=user_group)

        db_login = user_entry.get("portal_login")
        db_pw = user_entry.get("portal_password")

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
        with open(BANNERS["profile"], "rb") as photo:
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
            "🔗 *Привязка личного кабинета УрГЭУ*\n\nВведи свой ID пользователя от портала (например: `m.m.samosadov`):",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✕ Отмена", callback_data="portal_cancel")]
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

        text = (
        "🔗 Аккаунт  —  ⚪ не привязан\n\n"
        "🔥 *Дедлайны* — тесты и задания со сроками\n"
        "🏆 *Рейтинг* — ваши баллы по предметам\n\n"
        "_Привяжите аккаунт, чтобы смотреть всё это прямо в боте._"
        )
        await swap_banner(query, text, get_portal_keyboard(False), "portal")
        return
    elif data == "unlink_group":
        await query.answer("Группа отвязана")

        # 1. Очищаем группу из оперативной памяти
        context.user_data.pop("user_group", None)
        context.user_data.pop("subgroup", None)
        context.user_data["waiting_for_group"] = True

        # 2. Удаляем группу из файла users.json
        users = get_users_cache()
        uid_str = str(chat_id)
        if uid_str in users:
            users[uid_str].pop("user_group", None)
            users[uid_str].pop("subgroup", None)
            await save_user_db(uid_str, users[uid_str])
        # 3. Отправляем аккуратный запрос на ввод новой группы
        prompt_msg = await context.bot.send_message(
            chat_id=chat_id,
            text="✏️ *Введи номер новой группы:*\n_(Например: `ПИЭ-26-2` или `АИС-26-1`)_",
            parse_mode="Markdown"
        )
        context.user_data["group_prompt_msg_id"] = prompt_msg.message_id
        return

    elif data in ("portal_rating", "portal_rating_refresh"):

        users = get_users_cache()

        user_entry = users.get(str(chat_id), {})

        login = user_entry.get("portal_login") or context.user_data.get("portal_login")

        enc_pw = user_entry.get("portal_password") or context.user_data.get("portal_password")

        if not login or not enc_pw:
            await query.answer("Сначала привяжи аккаунт портала!", show_alert=True)
            return

        if data == "portal_rating_refresh" or "успешно привязан" in (query.message.text or ""):
            try:
                await query.message.delete()
            except Exception:
                pass
        status_msg = await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="⏳ <b>Подключаюсь к порталу УрГЭУ и считаю баллы...</b>",
            parse_mode="HTML"
        )

        try:
            raw_password = decrypt_password(enc_pw)
            success, rating_data, grades_dict = await get_portal_rating(login, raw_password)
        except Exception as e:
            success, rating_data, grades_dict = False, f"Ошибка дешифрования данных: {e}", {}

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("↻ Обновить", callback_data="portal_rating_refresh")],
            [InlineKeyboardButton("✕ Закрыть", callback_data="close_rating")]
        ])

        if success:
            users = get_users_cache()
            uid = str(query.from_user.id)
            prev_grades = users.get(uid, {}).get("last_rating", {})

            rating_html = build_rating_html(rating_data, prev_grades)

            # сохраняем свежие баллы для следующего сравнения
            if uid in users:
                users[uid]["last_rating"] = grades_dict
                await save_user_db(uid, users[uid])

            try:
                await send_rich_html(query.message.chat_id, rating_html, reply_markup=kb)
                await status_msg.delete()
            except Exception as e:
                # запасной вариант: старый вид, если rich не сработал
                print(f"[WARN] rich rating failed: {e}")
                lines = ["<b>Текущий академический рейтинг:</b>\n"]
                for item in rating_data:
                    lines.append(
                        f"• <b>{html.escape(str(item['name']))}</b> — "
                        f"<code>{html.escape(str(item['raw_str']))}</code>"
                    )
                await status_msg.edit_text("\n".join(lines), parse_mode="HTML", reply_markup=kb)
        else:
            await status_msg.edit_text(html.escape(str(rating_data)), parse_mode="HTML", reply_markup=kb)
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
            "📸 *Смена фото профиля*\n\n"
            "Пришлите мне фотографию следующим сообщением, и она будет установлена в вашем личном кабинете!"
        )
        with open(BANNERS["profile"], "rb") as photo:
            await query.edit_message_media(
                media=InputMediaPhoto(media=photo, caption=text, parse_mode="Markdown"),
                reply_markup=SCHEDULE_KEYBOARD,
            )
        return
    elif data == "main_menu":
        context.user_data["waiting_for_avatar"] = False
        context.user_data["waiting_for_date"] = False
        context.user_data["waiting_for_feedback"] = False

        users = get_users_cache()
        user_entry = users.get(str(chat_id), {})

        user_name = query.from_user.first_name
        user_group = user_entry.get("user_group") or context.user_data.get("user_group") or DEFAULT_GROUP
        subgroup = user_entry.get("subgroup") or context.user_data.get("subgroup")

        text = get_welcome_text(user_name, subgroup, group=user_group)
        reply_markup = get_main_keyboard(subgroup)
        banner_key = "main"

    # 5. Подгруппы
    elif data == "choose_subgroup":
        text = (
            "👥 *Выбор подгруппы*\n\n"
            "Укажите номер вашей подгруппы 👇"
        )
        reply_markup = SUBGROUP_KEYBOARD
        banner_key = "subgroup"

    elif data in ("set_subgroup_1", "set_subgroup_2"):
        val = 1 if data == "set_subgroup_1" else 2
        context.user_data["subgroup"] = val
        subgroup = val

        # Сохраняем подгруппу в базу пользователей (для личного кабинета)
        users = get_users_cache()
        uid = str(chat_id)
        if uid in users:
            users[uid]["subgroup"] = val
            await save_user_db(str(chat_id), users[str(chat_id)])

        # Сохраняем в уведомления
        notifs = get_notifs_cache()
        if uid in notifs:
            notifs[uid]["subgroup"] = val
            from db import save_notif_db
            await save_notif_db(uid, notifs[uid])

        user_name = query.from_user.first_name
        current_group = context.user_data.get("user_group") or DEFAULT_GROUP
        text = (
            f"✅ *Подгруппа {val} установлена*\n\n"
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
                f"🎫 *Обращение #{active_t['id']}*  ·  🟢 в обработке\n"
                f"📅 Создано: *{active_t['created_at']}*\n\n"
                f"💬 Последнее сообщение ({sender_label}):\n"
                f"> {last_msg['text']}\n\n"
                "Можно отправить дополнение или закрыть обращение, если вопрос решён."
            )
            reply_markup = get_support_keyboard(has_active_ticket=True)
        else:
            text = (
                "💬 *Нашли ошибку или есть идея?*\n\n"
                "🐞 Ошибка в расписании, кабинете или у преподавателя\n"
                "💡 Предложение, как сделать бота лучше\n\n"
                "Нажмите кнопку ниже и создайте обращение — администратор увидит его и ответит."
            )
            reply_markup = get_support_keyboard(has_active_ticket=False)

        with open(BANNERS["feedback"], "rb") as photo:
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
            "✏️ *Напишите текст сообщения*\n\n"
            "Отправьте сообщение боту следующим текстом. Оно автоматически прикрепится к вашему обращению."
        )
        cancel_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✕ Отмена", callback_data="feedback")]
        ])
        with open(BANNERS["feedback"], "rb") as photo:
            await query.edit_message_media(
                media=InputMediaPhoto(media=photo, caption=text, parse_mode="Markdown"),
                reply_markup=cancel_kb,
            )
        return

    elif data == "ticket_close_self":
        active_t = get_active_ticket(chat_id)
        if active_t:
            close_ticket_by_id(active_t["id"])

        text = "✅ *Ваше обращение закрыто.*\n\nВы всегда можете посмотреть его в разделе «Мои обращения»."
        reply_markup = get_support_keyboard(has_active_ticket=False)
        with open(BANNERS["feedback"], "rb") as photo:
            await query.edit_message_media(
                media=InputMediaPhoto(media=photo, caption=text, parse_mode="Markdown"),
                reply_markup=reply_markup,
            )
        return

    elif data == "ticket_history":
        text = (
            "📁 *Мои обращения*\n\n"
            "Выберите тикет для просмотра истории переписки 👇"
        )
        reply_markup = get_user_tickets_keyboard(chat_id)
        with open(BANNERS["feedback"], "rb") as photo:
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
            status = "🟢 в обработке" if ticket.get("status") == "open" else "⚪ закрыто"
            text_lines = [
                f"🎫 *Обращение #{ticket['id']}*  ·  {status}",
                f"📅 Дата: *{ticket['created_at']}*",
                DIV
            ]
            for msg in ticket.get("messages", []):
                role = "👤 Вы" if msg["sender"] == "user" else "🛠 Поддержка"
                text_lines.append(f"*{role}* ({msg.get('time', '')}):\n{msg['text']}\n")

            text = "\n".join(text_lines)
            kb = [[InlineKeyboardButton("‹ К списку обращений", callback_data="ticket_history")]]
            reply_markup = InlineKeyboardMarkup(kb)

        with open(BANNERS["feedback"], "rb") as photo:
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
            f"✏️ Напишите ответ на тикет #{ticket_id} следующим сообщением:"
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
                await query.edit_message_text(f"❌ Тикет #{ticket_id} успешно закрыт.")
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
            "🔎 *Введите число и месяц* в формате `ДД.ММ`\n\n"
            "📝 Например: `28.10` или `05.11`"
        )
        reply_markup = SCHEDULE_KEYBOARD
        banner_key = "date"

    elif data == "onboarding_set_group":
        await query.answer()
        context.user_data["waiting_for_group"] = True
        prompt_msg = await context.bot.send_message(
            chat_id=chat_id,
            text="✏️ *Введи номер своей группы:*\n_(Например: `ПИЭ-26-2` или `АИС-26-1`)_",
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
            text = "📋 *Расписание на неделю — отдельным сообщением ниже* 👇"
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

    # --- 1. ПРОВЕРКА ВВОДА ВРЕМЕНИ ДЛЯ УВЕДОМЛЕНИЙ ---
    if context.user_data.get("waiting_for_notif_time"):
        # Сразу удаляем сообщение пользователя с цифрами
        try:
            await update.message.delete()
        except Exception:
            pass

        import re
        if re.match(r"^([01]?[0-9]|2[0-3]):[0-5][0-9]$", text):
            uid = str(update.effective_user.id)
            from db import get_notifs_cache, save_notif_db
            notifs = get_notifs_cache()

            if uid not in notifs:
                notifs[uid] = {}

            notifs[uid]["enabled"] = True

            parts = text.split(':')
            formatted_time = f"{int(parts[0]):02d}:{parts[1]}"
            notifs[uid]["time"] = formatted_time

            await save_notif_db(uid, notifs[uid])
            context.user_data["waiting_for_notif_time"] = False

            # Меняем текст самого меню, чтобы не плодить сообщения
            menu_msg_id = context.user_data.get("main_menu_msg_id")
            if menu_msg_id:
                try:
                    # Убедись, что get_notif_keyboard импортирована или доступна здесь
                    await context.bot.edit_message_caption(
                        chat_id=chat_id,
                        message_id=menu_msg_id,
                        caption=f"✅ Время успешно установлено на {formatted_time}!",
                        reply_markup=get_notif_keyboard(formatted_time)
                    )
                except Exception as e:
                    print(f"Ошибка обновления меню: {e}")
        else:
            # Ошибку тоже делаем временной
            err_msg = await update.message.reply_text("❌ Ошибка. Введите время в формате ЧЧ:ММ (например, 20:30).")
            import asyncio
            await asyncio.sleep(3)
            try:
                await err_msg.delete()
            except:
                pass
        return
        # -------------------------------------------------


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
        users = get_users_cache()
        uid_str = str(chat_id)
        if uid_str not in users:
            users[uid_str] = {}
        users[uid_str]["user_group"] = entered_group
        users[uid_str].pop("subgroup", None)
        await save_user_db(uid_str, users[uid_str])
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
                    f"📩 *Поступил ответ по обращению #{target_ticket_id}!*\n\n"
                    f"Ответ администратора:\n> {text}\n\n"
                    f"Вы можете посмотреть историю в разделе «Поддержка»."
                )
                with open(BANNERS["feedback"], "rb") as photo:
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
                users = get_users_cache()
                user_entry = users.setdefault(str(chat_id), {})
                user_entry["portal_login"] = login
                user_entry["portal_password"] = enc_pw
                await save_user_db(str(chat_id), users[str(chat_id)])

                # Дублируем данные в оперативную память для моментального отклика
                context.user_data["portal_login"] = login
                context.user_data["portal_password"] = enc_pw
                context.user_data["waiting_for_portal_password"] = False
                context.user_data.pop("temp_portal_login", None)

                ok_kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("🏆 Посмотреть рейтинг", callback_data="portal_rating")],
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
                    [InlineKeyboardButton("↻ Попробовать снова", callback_data="portal_bind")],
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

                banner_img = BANNERS.get("date") or BANNERS["date"]
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
                        f"📩 *Новое сообщение в тикете #{ticket_id}*\n"
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
            f"✅ *Сообщение добавлено в обращение #{ticket_id}!*\n\n"
            f"Администратор уже получил ваше уведомление.\n"
            f"Вы сможете посмотреть ответ прямо в этом меню."
        )
        reply_markup = get_support_keyboard(has_active_ticket=True)

        if menu_id:
            try:
                with open(BANNERS["feedback"], "rb") as photo:
                    await context.bot.edit_message_media(
                        chat_id=chat_id,
                        message_id=menu_id,
                        media=InputMediaPhoto(media=photo, caption=card_text, parse_mode="Markdown"),
                        reply_markup=reply_markup,
                    )
                return
            except Exception:
                pass

        with open(BANNERS["feedback"], "rb") as photo:
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

        users = get_users_cache()
        uid = str(user.id)
        if uid not in users:
            await track_user_activity(user, action_name="set_avatar")
            users = get_users_cache()

        if uid not in users:
            users[uid] = {}
        users[uid]["avatar_file_id"] = avatar_file_id

        users[uid]["avatar_file_id"] = avatar_file_id
        await save_user_db(str(chat_id), users[str(chat_id)])

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

    users = get_users_cache()
    uid = str(user.id)
    if uid not in users:
        await track_user_activity(user, action_name="set_avatar")
        users = get_users_cache()

    if uid not in users:
        users[uid] = {}
    users[uid]["avatar_file_id"] = avatar_file_id
    await save_user_db(str(chat_id), users[str(chat_id)])
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

async def on_startup(application):
    print("Инициализация базы данных...")
    await init_db()

# ==========================================
# ХАБ УВЕДОМЛЕНИЙ И ДЕДЛАЙНОВ
# ==========================================

async def show_notifications_hub(query, context):
    text = (
    "🔔 <b>Выберите, что настроить:</b>\n\n"
    "<blockquote>"
    "🔥 <b>Дедлайны (портал)</b> — новые задания, тесты и сроки сдачи\n"
    "⏰ <b>Уведомления о парах</b> — утренний дайджест и напоминания перед началом"
    "</blockquote>"
    )
    keyboard = [
        [InlineKeyboardButton("🔥 Дедлайны и тесты", callback_data="deadline_settings")],
        [InlineKeyboardButton("⏰ Напоминания о парах", callback_data="schedule_notify_settings")],
        [InlineKeyboardButton("‹ В главное меню", callback_data="back_to_main")]
    ]
    with open(BANNERS["notifications"], "rb") as photo:
        await query.edit_message_media(
            media=InputMediaPhoto(media=photo, caption=text, parse_mode="HTML"),
            reply_markup=InlineKeyboardMarkup(keyboard)
        )


async def show_deadline_settings(query, context, toggle_key=None):
    user_id = str(query.from_user.id)
    users = get_users_cache()
    user = users.setdefault(user_id, {})

    settings = user.setdefault("deadline_notify", {
        "new": True,
        "24h": True,
        "3h": True
    })

    if toggle_key and toggle_key in settings:
        settings[toggle_key] = not settings[toggle_key]
        await save_user_db(user_id, users[user_id])
    btn_new = "🟢 Новые задания и тесты" if settings.get("new", True) else "⚪ Новые задания и тесты"
    btn_24h = "🟢 Напоминать за 24 часа" if settings.get("24h", True) else "⚪ Напоминать за 24 часа"
    btn_3h = "🟢 Напоминать за 3 часа" if settings.get("3h", True) else "⚪ Напоминать за 3 часа"

    text = (
    "🔥 <b>Уведомления о дедлайнах</b>\n\n"
    "Нажмите на пункт, чтобы включить или выключить:\n"
    "<blockquote>"
    "🆕 <b>Новые задания и тесты</b> — сразу при появлении на портале\n"
    "⏰ <b>За 24 часа</b> — предупреждение за сутки до срока\n"
    "🔥 <b>За 3 часа</b> — финальное напоминание"
    "</blockquote>"
    )

    keyboard = [
        [InlineKeyboardButton(btn_new, callback_data="set_deadline:new")],
        [InlineKeyboardButton(btn_24h, callback_data="set_deadline:24h")],
        [InlineKeyboardButton(btn_3h, callback_data="set_deadline:3h")],
        [InlineKeyboardButton("‹ Назад в уведомления", callback_data="notif_menu")]
    ]
    with open(BANNERS["notifications"], "rb") as photo:
        await query.edit_message_media(
            media=InputMediaPhoto(media=photo, caption=text, parse_mode="HTML"),
            reply_markup=InlineKeyboardMarkup(keyboard)
        )


async def show_schedule_notify_settings(query, context, toggle_key=None):
    user_id = str(query.from_user.id)
    users = get_users_cache()
    user = users.setdefault(user_id, {})

    settings = user.setdefault("schedule_notify", {
        "morning": True,
        "before_pair": True
    })

    if toggle_key and toggle_key in settings:
        settings[toggle_key] = not settings[toggle_key]
        await save_user_db(user_id, users[user_id])

    btn_morning = "🟢 Утренний план (07:30)" if settings.get("morning", True) else "⚪ Утренний план (07:30)"
    btn_before = "🟢 За 15 минут до пары" if settings.get("before_pair", True) else "⚪ За 15 минут до пары"

    text = (
    "⏰ <b>Уведомления о парах</b>\n\n"
    "<blockquote>"
    "🌅 <b>Утренний план</b> — список пар на сегодня каждое утро\n"
    "🔔 <b>За 15 минут</b> — предупреждение перед парой с аудиторией"
    "</blockquote>"
    )

    keyboard = [
        [InlineKeyboardButton(btn_morning, callback_data="set_sched:morning")],
        [InlineKeyboardButton(btn_before, callback_data="set_sched:before_pair")],
        [InlineKeyboardButton("‹ Назад в уведомления", callback_data="notif_menu")]
    ]
    with open(BANNERS["notifications"], "rb") as photo:
        await query.edit_message_media(
            media=InputMediaPhoto(media=photo, caption=text, parse_mode="HTML"),
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
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
    except Exception as e:
        print(f"[WARN] Не удалось запустить фоновые задачи: {e}")

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CommandHandler("stats", admin_command))
    app.add_handler(CommandHandler("broadcast", broadcast_command))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_user_text))
    app.add_handler(MessageHandler(filters.PHOTO, handle_user_photo))

    print("✅ Бот успешно запущен и ожидает сообщений!")
    app.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    main()