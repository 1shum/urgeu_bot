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

    subgroup_header = f" `[Подгруппа {subgroup}]`" if subgroup else ""
    lines = [
        f"📅 **{weekday.upper()}** ({date_str})",
        f"📚 Группа: `{group_name}`{subgroup_header}",
        "─" * 20,
    ]

    has_pairs = False
    for pair in day_data.get("pairs", []):
        sub_pairs = pair.get("schedulePairs", [])
        if not sub_pairs:
            continue

        # Фильтруем пары по подгруппе
        filtered = []
        for item in sub_pairs:
            item_group = item.get("group", "")
            if subgroup == 1 and ("(2" in item_group or "2 подгр" in item_group.lower()):
                continue
            if subgroup == 2 and ("(1" in item_group or "1 подгр" in item_group.lower()):
                continue
            filtered.append(item)

        if not filtered:
            continue

        has_pairs = True
        num = pair.get("N", "")
        time_slot = pair.get("time", "")

        lines.append(f"\n*{num} пара* | `{time_slot}`")

        for item in filtered:
            subj = item.get("subject", "Предмет не указан").strip()
            teacher = item.get("teacher", "").strip()
            aud = item.get("aud", "").strip()

            details = []
            if aud:
                details.append(f"ауд. {aud}")
            if teacher:
                details.append(teacher)

            info = f" ({', '.join(details)})" if details else ""
            lines.append(f"• **{subj}**{info}")

    if not has_pairs:
        lines.append("\n🎉 Пар нет, занятий не запланировано.")

    lines.append("\n" + "─" * 20)
    return "\n".join(lines)


def get_schedule_for_date(group: str, target_date: datetime, subgroup: int | None = None) -> str:
    date_str = target_date.strftime("%d.%m.%Y")
    data = fetch_raw_schedule(group, date_str, date_str)

    if not data:
        return f"⚠️ Не удалось загрузить расписание на *{date_str}*."

    return format_day_schedule(data[0], group, subgroup)


def get_schedule_for_week(
    group: str, next_week: bool = False, subgroup: int | None = None
) -> str:
    """Компактное недельное расписание без лишнего визуального шума."""
    today = datetime.now()
    # Вычисляем понедельник недели
    start_of_week = today - timedelta(days=today.weekday())
    if next_week:
        start_of_week += timedelta(days=7)
    end_of_week = start_of_week + timedelta(days=6)

    start_str = start_of_week.strftime("%d.%m.%Y")
    end_str = end_of_week.strftime("%d.%m.%Y")

    data = fetch_raw_schedule(group, start_str, end_str)
    if not data:
        return "⚠️ Не удалось получить расписание на неделю."

    subgroup_tag = f" `[Подгруппа {subgroup}]`" if subgroup else ""
    lines = [
        f"🗓 **НЕДЕЛЯ: {start_str[:5]} — {end_str[:5]}**",
        f"📚 Группа: `{group}`{subgroup_tag}",
        "─" * 20,
    ]

    has_any_lesson = False

    for day in data:
        weekday = day.get("weekDay", "").capitalize()
        date_str = day.get("date", "")
        day_pairs = []

        for pair in day.get("pairs", []):
            sub_pairs = pair.get("schedulePairs", [])
            for item in sub_pairs:
                item_group = item.get("group", "")
                if subgroup == 1 and ("(2" in item_group or "2 подгр" in item_group.lower()):
                    continue
                if subgroup == 2 and ("(1" in item_group or "1 подгр" in item_group.lower()):
                    continue

                subj = item.get("subject", "").strip()
                aud = item.get("aud", "").strip()
                t_slot = pair.get("time", "").split("-")[0].strip()  # только время начала

                aud_str = f" `ауд.{aud}`" if aud else ""
                day_pairs.append(f"`{t_slot}` {subj}{aud_str}")

        if day_pairs:
            has_any_lesson = True
            lines.append(f"\n*{weekday}* ({date_str[:5]}):")
            for p_str in day_pairs:
                lines.append(f"  • {p_str}")

    if not has_any_lesson:
        lines.append("\nНа этой неделе пар нет.")

    return "\n".join(lines)

def get_short_schedule_for_date(
    group: str, target_date: datetime, subgroup: int | None = None
) -> str:
    """Генерирует ультракороткую сводку расписания для пуш-уведомлений."""
    date_str = target_date.strftime("%d.%m.%Y")
    data = fetch_raw_schedule(group, date_str, date_str)

    if not data or not data[0].get("pairs"):
        return f"🔔 Завтра ({target_date.strftime('%d.%m')}): пар нет! Отдыхай 🎉"

    day_data = data[0]
    pairs = day_data.get("pairs", [])

    valid_pairs = []
    first_time = None
    last_time = None

    for pair in pairs:
        sub_pairs = pair.get("schedulePairs", [])
        filtered = []
        for item in sub_pairs:
            item_group = item.get("group", "")
            if subgroup == 1 and (
                "(2" in item_group or "2 подгр" in item_group.lower()
            ):
                continue
            if subgroup == 2 and (
                "(1" in item_group or "1 подгр" in item_group.lower()
            ):
                continue
            filtered.append(item)

        if filtered:
            subject = filtered[0].get("subject", "").strip()
            # Укорачиваем длинные названия предметов при необходимости
            if len(subject) > 28:
                subject = subject[:25] + "..."
            valid_pairs.append(subject)

            time_range = pair.get("time", "")
            if "-" in time_range:
                start_p, end_p = [t.strip() for t in time_range.split("-")]
                if not first_time:
                    first_time = start_p
                last_time = end_p

    if not valid_pairs:
        return f"🔔 Завтра ({target_date.strftime('%d.%m')}): пар нет! Отдыхай 🎉"

    date_short = target_date.strftime("%d.%m")
    lines = [f"🔔 *Завтра ({date_short})* — *{len(valid_pairs)} пар(ы):*"]

    for i, subj in enumerate(valid_pairs, 1):
        lines.append(f"{i}. {subj}")

    if first_time and last_time:
        lines.append(f"⏰ `{first_time} – {last_time}`")

    return "\n".join(lines)



