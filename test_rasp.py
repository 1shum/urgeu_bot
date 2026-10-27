from datetime import datetime, timedelta
from parser_rasp import get_schedule_for_date

group = "ПИЭ-26-2"
today = datetime.now()

print("--- РАСПИСАНИЕ НА СЕГОДНЯ ---")
print(get_schedule_for_date(group, today))

print("\n--- РАСПИСАНИЕ НА ЗАВТРА ---")
print(get_schedule_for_date(group, today + timedelta(days=1)))
