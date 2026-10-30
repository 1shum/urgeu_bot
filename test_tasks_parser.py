import asyncio
import aiohttp
from bs4 import BeautifulSoup
from datetime import datetime
from urllib.parse import urljoin

LOGIN = "m.m.samosadov"
PASSWORD = "Shum2008"
BASE_URL = "https://portal.usue.ru"


def parse_deadline(date_str):
    """Преобразует дату портала в объект datetime"""
    # 1. Формат '31.10.2026 17:00'
    try:
        return datetime.strptime(date_str.strip(), "%d.%m.%Y %H:%M")
    except Exception:
        pass

    # 2. Формат '2026-окт-31 02:44:05 PM'
    ru_months = {
        "янв": "01", "фев": "02", "мар": "03", "апр": "04",
        "май": "05", "июн": "06", "июл": "07", "авг": "08",
        "сен": "09", "окт": "10", "ноя": "11", "дек": "12"
    }
    cleaned = date_str.strip().lower()
    for ru, num in ru_months.items():
        if f"-{ru}-" in cleaned:
            cleaned = cleaned.replace(f"-{ru}-", f"-{num}-")
            break
    try:
        return datetime.strptime(cleaned, "%Y-%m-%d %I:%M:%S %p")
    except Exception:
        return None


async def find_all_courses_and_tasks():
    login_url = f"{BASE_URL}/portal/xlogin"
    login_data = {"eid": LOGIN, "pw": PASSWORD, "submit": "Вход"}
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}

    connector = aiohttp.TCPConnector(ssl=False)
    jar = aiohttp.CookieJar(unsafe=True)

    ignore_titles = {
        "мой сайт", "выход из системы", "вход в систему", "профиль",
        "запись на курсы", "календарь", "ресурсы", "объявления",
        "мои сайты", "мои настройки", "моя учетная запись", "сведения об обучении"
    }

    async with aiohttp.ClientSession(headers=headers, connector=connector, cookie_jar=jar) as session:
        # 1. Авторизация
        await session.get(f"{BASE_URL}/portal")
        login_res = await session.post(login_url, data=login_data, allow_redirects=True)
        portal_html = await login_res.text()

        # 2. Собираем реальные курсы
        soup = BeautifulSoup(portal_html, "html.parser")
        courses = []

        for a in soup.find_all("a", href=True):
            raw_href = a["href"].strip()
            title = a.get_text(strip=True)

            # Пропускаем служебные страницы и персональный раздел
            if not title or title.lower() in ignore_titles:
                continue
            if "~" in raw_href or "!gateway" in raw_href:
                continue

            if "/portal/site/" in raw_href:
                full_url = urljoin(BASE_URL, raw_href)
                if not any(c["url"] == full_url for c in courses):
                    courses.append({"title": title, "url": full_url})

        print(f"🎯 Найдено учебных курсов для сканирования: {len(courses)}")
        for c in courses:
            print(f"  • {c['title']}")

        # 3. Обходим каждый курс
        all_active_deadlines = []
        now = datetime.now()

        for c in courses:
            site_url = c["url"]
            try:
                async with session.get(site_url) as resp:
                    site_html = await resp.text()
            except Exception as e:
                print(f"Ошибка загрузки курса {c['title']}: {e}")
                continue

            course_soup = BeautifulSoup(site_html, "html.parser")

            # Ищем ссылки на "Задания" и "Тесты и экзамены" в левом меню
            tool_links = {}
            for a in course_soup.find_all("a", href=True):
                text = a.get_text(strip=True).lower()
                href = urljoin(BASE_URL, a["href"].strip())
                if "задания" in text and "/portal/site/" in href:
                    tool_links["tasks"] = href
                elif ("тесты" in text or "экзамены" in text) and "/portal/site/" in href:
                    tool_links["tests"] = href

            # Проверяем инструменты курса
            for tool_type, tool_page_url in tool_links.items():
                try:
                    async with session.get(tool_page_url) as resp:
                        page_html = await resp.text()

                    p_soup = BeautifulSoup(page_html, "html.parser")
                    iframe = p_soup.find("iframe")
                    if not iframe or not iframe.get("src"):
                        continue

                    iframe_url = urljoin(BASE_URL, iframe["src"].strip())
                    async with session.get(iframe_url) as resp:
                        frame_html = await resp.text()

                    f_soup = BeautifulSoup(frame_html, "html.parser")

                    # Парсинг ЗАДАНИЙ
                    if tool_type == "tasks":
                        for tr in f_soup.find_all("tr"):
                            cols = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
                            if len(cols) >= 4 and cols[0] != "Название приложения":
                                title, status, deadline_raw = cols[0], cols[1], cols[3]
                                if "сдано" not in status.lower():
                                    dt = parse_deadline(deadline_raw)
                                    if dt and dt > now:
                                        all_active_deadlines.append({
                                            "course": c["title"],
                                            "type": "Задание",
                                            "title": title,
                                            "status": status,
                                            "deadline": dt,
                                            "raw_date": deadline_raw
                                        })

                    # Парсинг ТЕСТОВ
                    elif tool_type == "tests":
                        for tr in f_soup.find_all("tr"):
                            cols = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
                            if len(cols) >= 3 and cols[0] not in ["Название", "Пройти тест"]:
                                title, deadline_raw = cols[0], cols[2]
                                dt = parse_deadline(deadline_raw)
                                if dt and dt > now:
                                    all_active_deadlines.append({
                                        "course": c["title"],
                                        "type": "Тест",
                                        "title": title,
                                        "status": "Не сдан",
                                        "deadline": dt,
                                        "raw_date": deadline_raw
                                    })
                except Exception:
                    pass

        print("\n================== ВСЕ АКТУАЛЬНЫЕ ДЕДЛАЙНЫ ==================")
        if not all_active_deadlines:
            print("🎉 Горящих заданий и тестов не найдено!")
        else:
            for item in sorted(all_active_deadlines, key=lambda x: x["deadline"]):
                diff = item["deadline"] - now
                days = diff.days
                hours = int(diff.seconds // 3600)
                print(f"[{item['type']}] {item['course']} ➜ {item['title']}")
                print(f"   Срок: {item['raw_date']} (осталось: {days} дн. {hours} ч.) | Статус: {item['status']}")
                print("-" * 50)


if __name__ == "__main__":
    asyncio.run(find_all_courses_and_tasks())