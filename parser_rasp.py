from datetime import datetime, timedelta
import requests

API_URL = "https://www.usue.ru/schedule/"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        " (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "X-Requested-With": "XMLHttpRequest",
}


def fetch_raw_schedule(
    group: str, start_date: str, end_date: str
) -> list | None:
    params = {
        "action": "show",
        "startDate": start_date,
        "endDate": end_date,
        "group": group,
    }
    try:
        response = requests.get(
            API_URL, params=params, headers=HEADERS, timeout=7
        )
        if response.status_code == 200:
            return response.json()
    except Exception as e:
        print(f"Ошибка запроса расписания: {e}")
    return None


def format_day_schedule(day_data: dict, group_name: str, subgroup: int | None = None) -> str:
    date_str = day_data.get("date", "")
    weekday = day_data.get("weekDay", "")

    subgroup_header = f" (Подгруппа {subgroup})" if subgroup else ""
    lines = [
        f"📅 *{weekday}* ({date_str})",
        f"👥 Группа: `{group_name}`{subgroup_header}",
        "━━━━━━━━━━━━━━━━━━━━",
    ]

    has_pairs = False
    for pair in day_data.get("pairs", []):
        sub_pairs = pair.get("schedulePairs", [])
        if not sub_pairs:
            continue

        # Фильтруем пары по подгруппе
        filtered_sub_pairs = []
        for item in sub_pairs:
            item_group = item.get("group", "")
            # Если выбрана 1-я подгруппа, отсекаем пары 2-й
            if subgroup == 1 and ("(2" in item_group or "2 подгр" in item_group.lower()):
                continue
            # Если выбрана 2-я подгруппа, отсекаем пары 1-й
            if subgroup == 2 and ("(1" in item_group or "1 подгр" in item_group.lower()):
                continue
            filtered_sub_pairs.append(item)

        if not filtered_sub_pairs:
            continue

        has_pairs = True
        num = pair.get("N")
        time_slot = pair.get("time")

        lines.append(f"\n*{num} пара* | ⏰ `{time_slot}`")

        for item in filtered_sub_pairs:
            subj = item.get("subject", "Предмет не указан")
            teacher = item.get("teacher", "Преподаватель не указан")
            aud = item.get("aud", "—")
            sub_group = item.get("group", "")

            group_tag = f" _({sub_group})_" if "(" in sub_group else ""
            lines.append(f"📘 *{subj}*{group_tag}")
            lines.append(f"📍 Ауд. *{aud}* | 👤 {teacher}")

    if not has_pairs:
        lines.append("\n🎉 *В этот день пар нет! Отдыхай.*")

    lines.append("\n━━━━━━━━━━━━━━━━━━━━")
    return "\n".join(lines)


def get_schedule_for_date(group: str, target_date: datetime, subgroup: int | None = None) -> str:
    date_str = target_date.strftime("%d.%m.%Y")
    data = fetch_raw_schedule(group, date_str, date_str)

    if not data:
        return f"⚠️ Не удалось загрузить расписание на *{date_str}*."

    return format_day_schedule(data[0], group, subgroup)


def format_pair(pair: dict) -> str:
    """Лаконичный вывод одной пары: время, название, аудитория/тип."""
    time_str = pair.get("time", "")
    subject = pair.get("subject", "").strip()
    room = pair.get("room", "").strip()
    pair_type = pair.get("type", "").strip()

    details = []
    if pair_type:
        details.append(pair_type)
    if room:
        details.append(f"ауд. {room}")

    extra = f" ({', '.join(details)})" if details else ""
    return f"`{time_str}` **{subject}**{extra}"


def get_schedule_for_week(
    group: str, next_week: bool = False, subgroup: int = None
) -> str:
    """Компактное расписание на неделю без лишних эмодзи и шума."""
    schedule_data = get_week_data(
        group, next_week=next_week
    )  # твоя функция получения данных недели
    if not schedule_data:
        return "Расписание на эту неделю отсутствует."

    lines = ["🗓 **РАСПИСАНИЕ НА НЕДЕЛЮ**", "—" * 15]

    for day in schedule_data:
        day_title = day.get("day_name", "").upper()
        date_str = day.get("date", "")
        pairs = day.get("pairs", [])

        # Фильтр по подгруппе, если задана
        if subgroup:
            pairs = [
                p
                for p in pairs
                if not p.get("subgroup") or p.get("subgroup") == subgroup
            ]

        lines.append(f"\n*{day_title}* (`{date_str}`)")

        if not pairs:
            lines.append("  Пар нет")
            continue

        for p in pairs:
            lines.append(f"• {format_pair(p)}")

    return "\n".join(lines)

