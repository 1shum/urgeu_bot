import re
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


# --- ОФОРМЛЕНИЕ -------------------------------------------------------------
# Единый стиль бота: ◆ заголовок, ▸ пункт, ─ разделитель, ▲ внимание.
DIV = "─" * 20


def _esc(value) -> str:
    """Экранирует служебные символы Markdown в данных расписания (предметы, ФИО, аудитории)."""
    return re.sub(r"([_*`\[])", r"\\\1", str(value))


def _fmt_time(slot: str) -> str:
    """08:30-10:00 -> 08:30 – 10:00"""
    return re.sub(r"\s*[-–]\s*", " – ", slot.strip(), count=1) if slot else ""


def _short_date(date_str: str) -> str:
    """02.11.2026 -> 02.11"""
    return date_str[:5] if len(date_str) >= 10 and date_str[2:3] == "." else date_str


def _plural_pairs(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} пара"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return f"{n} пары"
    return f"{n} пар"


def _skip_by_subgroup(item: dict, subgroup: int | None) -> bool:
    """True, если пара относится к другой подгруппе (логика фильтра не менялась)."""
    item_group = item.get("group", "")
    if subgroup == 1 and ("(2" in item_group or "2 подгр" in item_group.lower()):
        return True
    if subgroup == 2 and ("(1" in item_group or "1 подгр" in item_group.lower()):
        return True
    return False


def _group_line(group_name: str, subgroup: int | None) -> str:
    sub = f"  ·  подгруппа {subgroup}" if subgroup else ""
    return f"Группа  `{group_name}`{sub}"


def format_day_schedule(day_data: dict, group_name: str, subgroup: int | None = None) -> str:
    date_str = _short_date(day_data.get("date", ""))
    weekday = day_data.get("weekDay", "")

    lines = [
        f"◆ *{_esc(weekday.upper())}*  ·  {date_str}",
        _group_line(group_name, subgroup),
        DIV,
    ]

    has_pairs = False
    for pair in day_data.get("pairs", []):
        sub_pairs = pair.get("schedulePairs", [])
        if not sub_pairs:
            continue

        filtered = [item for item in sub_pairs if not _skip_by_subgroup(item, subgroup)]
        if not filtered:
            continue

        has_pairs = True
        num = pair.get("N", "")
        time_slot = _fmt_time(pair.get("time", ""))

        lines.append(f"\n*{num} пара*  ·  `{time_slot}`")

        for item in filtered:
            subj = item.get("subject", "Предмет не указан").strip()
            teacher = item.get("teacher", "").strip()
            aud = item.get("aud", "").strip()

            lines.append(f"▸ *{_esc(subj)}*")

            details = []
            if aud:
                details.append(f"ауд. {_esc(aud)}")
            if teacher:
                details.append(_esc(teacher))
            if details:
                lines.append("   " + "  ·  ".join(details))

    if not has_pairs:
        lines.append("\nЗанятий нет — свободный день.")

    lines.append("\n" + DIV)
    return "\n".join(lines)


def get_schedule_for_date(group: str, target_date: datetime, subgroup: int | None = None) -> str:
    date_str = target_date.strftime("%d.%m.%Y")
    data = fetch_raw_schedule(group, date_str, date_str)

    if not data:
        return f"▲ Не удалось загрузить расписание на *{date_str}*."

    return format_day_schedule(data[0], group, subgroup)


def get_schedule_for_week(
    group: str, next_week: bool = False, subgroup: int | None = None
) -> str:
    """Компактное недельное расписание."""
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
        return "▲ Не удалось получить расписание на неделю."

    lines = [
        f"◆ *НЕДЕЛЯ*  ·  {start_str[:5]} — {end_str[:5]}",
        _group_line(group, subgroup),
        DIV,
    ]

    has_any_lesson = False

    for day in data:
        weekday = day.get("weekDay", "").capitalize()
        date_str = _short_date(day.get("date", ""))
        day_pairs = []

        for pair in day.get("pairs", []):
            for item in pair.get("schedulePairs", []):
                if _skip_by_subgroup(item, subgroup):
                    continue

                subj = _esc(item.get("subject", "").strip())
                aud = item.get("aud", "").strip()
                t_slot = pair.get("time", "").split("-")[0].strip()  # только время начала

                aud_str = f"  ·  ауд. {_esc(aud)}" if aud else ""
                day_pairs.append(f"`{t_slot}`  {subj}{aud_str}")

        if day_pairs:
            has_any_lesson = True
            lines.append(f"\n*{_esc(weekday)}*  ·  {date_str}")
            for p_str in day_pairs:
                lines.append(f"▸ {p_str}")

    if not has_any_lesson:
        lines.append("\nНа этой неделе пар нет.")

    return "\n".join(lines)


def get_short_schedule_for_date(
    group: str, target_date: datetime, subgroup: int | None = None
) -> str:
    """Короткая сводка расписания для пуш-уведомления."""
    date_str = target_date.strftime("%d.%m.%Y")
    date_short = target_date.strftime("%d.%m")
    data = fetch_raw_schedule(group, date_str, date_str)

    no_pairs = f"◆ *ЗАВТРА*  ·  {date_short}\n{DIV}\nПар нет — можно отдыхать."

    if not data or not data[0].get("pairs"):
        return no_pairs

    valid_pairs = []
    first_time = None
    last_time = None

    for pair in data[0].get("pairs", []):
        filtered = [
            item for item in pair.get("schedulePairs", [])
            if not _skip_by_subgroup(item, subgroup)
        ]

        if filtered:
            subject = filtered[0].get("subject", "").strip()
            # Укорачиваем длинные названия предметов при необходимости
            if len(subject) > 28:
                subject = subject[:25] + "…"
            valid_pairs.append(subject)

            time_range = pair.get("time", "")
            if "-" in time_range:
                start_p, end_p = [t.strip() for t in time_range.split("-", 1)]
                if not first_time:
                    first_time = start_p
                last_time = end_p

    if not valid_pairs:
        return no_pairs

    lines = [
        f"◆ *ЗАВТРА*  ·  {date_short}  ·  {_plural_pairs(len(valid_pairs))}",
        DIV,
    ]
    for i, subj in enumerate(valid_pairs, 1):
        lines.append(f"*{i}*  {_esc(subj)}")

    if first_time and last_time:
        lines.append(f"\nЗанятия  `{first_time} – {last_time}`")

    return "\n".join(lines)


def check_group_exists(group_name: str) -> bool:
    """
    Проверяет, существует ли группа в УрГЭУ,
    запрашивая расписание на текущую неделю.
    """
    try:
        today = datetime.now()
        start = (today - timedelta(days=30)).strftime("%Y-%m-%d")
        end = (today + timedelta(days=30)).strftime("%Y-%m-%d")
        data = fetch_raw_schedule(group=group_name.strip(), start_date=start, end_date=end)

        # Если API вернул непустой список дней или не пустой ответ
        if data and isinstance(data, list) and len(data) > 0:
            return True
        return False
    except Exception:
        return False



