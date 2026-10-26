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


def format_day_schedule(day_data: dict, group_name: str) -> str:
    date_str = day_data.get("date", "")
    weekday = day_data.get("weekDay", "")

    lines = [
        f"📅 *{weekday}* ({date_str})",
        f"👥 Группа: `{group_name}`",
        "━━━━━━━━━━━━━━━━━━━━",
    ]

    has_pairs = False
    for pair in day_data.get("pairs", []):
        sub_pairs = pair.get("schedulePairs", [])
        if not sub_pairs:
            continue

        has_pairs = True
        num = pair.get("N")
        time_slot = pair.get("time")

        lines.append(f"\n*{num} пара* | ⏰ `{time_slot}`")

        for item in sub_pairs:
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


def get_schedule_for_date(group: str, target_date: datetime) -> str:
    date_str = target_date.strftime("%d.%m.%Y")
    data = fetch_raw_schedule(group, date_str, date_str)

    if not data:
        return f"⚠️ Не удалось загрузить расписание на *{date_str}*."

    return format_day_schedule(data[0], group)


def get_schedule_for_week(group: str, next_week: bool = False) -> str:
    today = datetime.now()
    if next_week:
        today += timedelta(days=7)

    monday = today - timedelta(days=today.weekday())
    saturday = monday + timedelta(days=5)

    data = fetch_raw_schedule(
        group, monday.strftime("%d.%m.%Y"), saturday.strftime("%d.%m.%Y")
    )
    if not data:
        return "⚠️ Не удалось загрузить расписание на неделю."

    week_label = "следующую" if next_week else "текущую"
    result = [f"🗓 *Расписание на {week_label} неделю*\n"]

    for day in data:
        result.append(format_day_schedule(day, group))
        result.append("")

    return "\n".join(result)
