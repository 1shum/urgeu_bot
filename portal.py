import html as html_lib
import os
import traceback
from datetime import datetime
from urllib.parse import urljoin
import aiohttp
from bs4 import BeautifulSoup
from cryptography.fernet import Fernet

PORTAL_LOGIN_URL = "https://portal.usue.ru/portal/xlogin"
PORTAL_BASE_URL = "https://portal.usue.ru/portal"
PORTAL_PROXY = "http://p7Se6A:zxDB5D@160.116.216.13:8000"

# Ключ шифрования. Для продакшена на Render добавь переменную окружения PORTAL_SECRET_KEY
SECRET_KEY = os.getenv("PORTAL_SECRET_KEY")
if not SECRET_KEY:
    SECRET_KEY = b"kXwB7D0_G0yFq2u91lQzX7_zUqE8yS4v1_L0M5wN6rM="
else:
    SECRET_KEY = SECRET_KEY.encode()

cipher = Fernet(SECRET_KEY)


def encrypt_password(password: str) -> str:
    """Шифрует пароль перед сохранением."""
    return cipher.encrypt(password.encode()).decode()


def decrypt_password(token: str) -> str:
    """Расшифровывает пароль для выполнения запросов."""
    return cipher.decrypt(token.encode()).decode()


async def check_portal_auth(login: str, password: str) -> tuple[bool, str]:
    """Проверяет корректность связки логин/пароль на портале УрГЭУ."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
            " AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0"
            " Safari/605.1.15"
        ),
        "Referer": "https://portal.usue.ru/portal",
        "Origin": "https://portal.usue.ru",
    }
    payload = {"eid": login, "pw": password, "submit": "Вход"}

    connector = aiohttp.TCPConnector(ssl=False)
    jar = aiohttp.CookieJar()

    async with aiohttp.ClientSession(
        connector=connector, cookie_jar=jar, headers=headers
    ) as session:
        try:
            async with session.post(
                PORTAL_LOGIN_URL, data=payload, allow_redirects=True, proxy=PORTAL_PROXY
            ) as response:
                html = await response.text()
                if response.status != 200:
                    return (
                        False,
                        f"Ошибка сервера портала: код {response.status}",
                    )

                lower_html = html.lower()
                if (
                    "неверный вход" in lower_html
                    or "invalid login" in lower_html
                    or "login failed" in lower_html
                ):
                    return False, "Неверный ID пользователя или пароль."

                if (
                    "выход из системы" in lower_html
                    or "logout" in lower_html
                    or "мой сайт" in lower_html
                ):
                    return True, "Успешная авторизация"

                return False, "Не удалось подтвердить вход."
        except Exception as e:
            return False, f"Ошибка сети при подключении к порталу: {e}"


async def get_portal_rating(login: str, password: str) -> tuple:
    """
    Авторизуется на портале, переключает Wicket на 'Рейтинг'
    и собирает список дисциплин и процентов.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
        "Referer": "https://portal.usue.ru/portal",
        "Origin": "https://portal.usue.ru"
    }
    payload = {
        "eid": login,
        "pw": password,
        "submit": "Вход"
    }

    connector = aiohttp.TCPConnector(ssl=False)
    jar = aiohttp.CookieJar()

    async with aiohttp.ClientSession(connector=connector, cookie_jar=jar, headers=headers) as session:
        try:
            # 1. Логин
            async with session.post(PORTAL_LOGIN_URL, data=payload, allow_redirects=True, proxy=PORTAL_PROXY) as resp:
                if resp.status != 200:
                    return False, "Не удалось войти на портал.", {}

            # 2. Переходим на главную страницу студента
            home_url = f"https://portal.usue.ru/portal/site/~{login}"
            async with session.get(home_url, allow_redirects=True, proxy=PORTAL_PROXY) as resp:
                if resp.status != 200:
                    return False, f"Ошибка загрузки личного кабинета: {resp.status}", {}
                raw_home = await resp.read()
                soup_home = BeautifulSoup(raw_home.decode("utf-8", errors="ignore"), "html.parser")

            edu_link = None
            for a in soup_home.find_all("a", href=True):
                link_text = a.get_text(strip=True).lower()
                if "сведения об обучении" in link_text or "обучение" in link_text:
                    edu_link = a["href"]
                    break

            if edu_link:
                target_url = urljoin("https://portal.usue.ru", edu_link)
            else:
                target_url = home_url

            async with session.get(target_url, allow_redirects=True, proxy=PORTAL_PROXY) as resp:
                if resp.status != 200:
                    return False, f"Ошибка загрузки страницы обучения: {resp.status}", {}
                raw = await resp.read()
                soup_main = BeautifulSoup(raw.decode("utf-8", errors="ignore"), "html.parser")

            iframe = soup_main.find("iframe")
            if not iframe or not iframe.get("src"):
                return False, "Не найден фрейм сведений об обучении."

            tool_url = iframe.get("src")
            if not tool_url.startswith("http"):
                tool_url = urljoin("https://portal.usue.ru", tool_url)

            # 3. Вход во фрейм
            async with session.get(tool_url, allow_redirects=True, proxy=PORTAL_PROXY) as tool_resp:
                base_tool_url = str(tool_resp.url)
                tool_html = (await tool_resp.read()).decode("utf-8", errors="ignore")

            soup_tool = BeautifulSoup(tool_html, "html.parser")
            rating_link = soup_tool.find("a", id="idd") or soup_tool.find("a", id="id8") or soup_tool.find(
                lambda tag: tag.name == "a" and "Рейтинг" in tag.text
            )

            wicket_url_part = "?wicket:interface=:0:tabs:tandem-panel:tabs-container:tabs:3:link::IBehaviorListener:0:-1"
            if rating_link and rating_link.get("onclick"):
                onclick = rating_link.get("onclick")
                if "wicketAjaxGet('" in onclick:
                    wicket_url_part = onclick.split("wicketAjaxGet('")[1].split("'")[0]

            ajax_url = urljoin(base_tool_url, wicket_url_part)

            # 4. Запрос переключения вкладки Wicket
            ajax_headers = {
                "Wicket-Ajax": "true",
                "Wicket-Ajax-BaseURL": base_tool_url.split("?")[0],
                "Accept": "text/xml, text/html, */*",
                "X-Requested-With": "XMLHttpRequest"
            }

            async with session.get(ajax_url, headers=ajax_headers, allow_redirects=True, proxy=PORTAL_PROXY) as ajax_resp:
                ajax_raw = await ajax_resp.read()
                response_text = ajax_raw.decode("utf-8", errors="ignore")

            # 5. Разбор HTML/XML
            if "<ajax-response>" in response_text:
                soup_data = BeautifulSoup(response_text, "html.parser")
                full_content = "".join(comp.get_text() + "\n" for comp in soup_data.find_all("component"))
                final_soup = BeautifulSoup(html_lib.unescape(full_content), "html.parser")
            else:
                final_soup = BeautifulSoup(response_text, "html.parser")

            # 6. Четкое извлечение пар Предмет -> Процент
            all_divs = [d.get_text(strip=True) for d in final_soup.find_all("div") if d.get_text(strip=True)]

            rating_items = []
            grades_dict = {}

            for i, text in enumerate(all_divs):
                if text.startswith("Рейтинг текущий -"):
                    percent_raw = text.replace("Рейтинг текущий -", "").strip()

                    val_clean = percent_raw.replace("%", "").replace(",", ".").strip()
                    grade_val = None
                    try:
                        grade_val = float(val_clean)
                    except ValueError:
                        pass

                    if "%" in percent_raw:
                        percent_str = percent_raw
                    else:
                        percent_str = "нет баллов"

                    subject_name = "Предмет"
                    if i >= 2:
                        possible_subj = all_divs[i - 2]
                        if not any(skip in possible_subj.lower() for skip in
                                   ["текущий рейтинг", "преподаватель", "семестр"]):
                            subject_name = possible_subj
                    elif i >= 1:
                        if not any(skip in all_divs[i - 1].lower() for skip in ["преподаватель", "текущий рейтинг"]):
                            subject_name = all_divs[i - 1]

                    if "Элективные курсы по физической культуре" in subject_name:
                        subject_name = "Физическая культура"

                    if grade_val is not None:
                        grades_dict[subject_name] = grade_val

                    rating_items.append({"name": subject_name, "raw_str": percent_str, "val": grade_val})

            if not rating_items:
                return False, "Не удалось распознать баллы. Попробуйте позже.", {}

            return True, rating_items, grades_dict

        except Exception as e:
            return False, f"Ошибка при получении рейтинга: {e}", {}


ru_months = {
    "янв": "01", "фев": "02", "мар": "03", "апр": "04",
    "май": "05", "мая": "05", "июн": "06", "июл": "07",
    "авг": "08", "сен": "09", "окт": "10", "ноя": "11", "дек": "12"
}


def parse_deadline(date_str: str):
    if not date_str:
        return None
    cleaned = date_str.strip()

    # Заменяем русские месяцы на номера
    for ru, num in ru_months.items():
        if f"-{ru}-" in cleaned.lower():
            import re
            cleaned = re.sub(f"-{ru}-", f"-{num}-", cleaned, flags=re.IGNORECASE)
            break

    # Пробуем форматы даты с портала
    formats = [
        "%Y-%m-%d %I:%M:%S %p",
        "%Y-%m-%d %I:%M %p",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%d.%m.%Y %H:%M",
        "%d.%m.%Y %H:%M:%S",
        "%d.%m.%Y"
    ]
    for fmt in formats:
        try:
            return datetime.strptime(cleaned, fmt)
        except Exception:
            pass

    return None


async def fetch_portal_deadlines(login, password):
    base_url = "https://portal.usue.ru"
    login_url = f"{base_url}/portal/xlogin"
    login_data = {"eid": login, "pw": password, "submit": "Вход"}
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}

    connector = aiohttp.TCPConnector(ssl=False)
    jar = aiohttp.CookieJar(unsafe=True)

    ignore_titles = {
        "мой сайт", "выход из системы", "вход в систему", "профиль",
        "запись на курсы", "календарь", "ресурсы", "объявления",
        "мои сайты", "мои настройки", "моя учетная запись", "сведения об обучении"
    }

    results = []
    now = datetime.now()

    print(f"[DEBUG AUTH] Логин: '{login}', длина пароля: {len(password) if password else 0}")

    try:
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(headers=headers, connector=connector, cookie_jar=jar,
                                         timeout=timeout) as session:
            # 1. Авторизация
            login_res = await session.post(login_url, data=login_data, allow_redirects=True, proxy=PORTAL_PROXY)
            portal_html = await login_res.text()

            soup = BeautifulSoup(portal_html, "html.parser")
            courses = []

            for a in soup.find_all("a", href=True):
                raw_href = a["href"].strip()
                title = a.get_text(strip=True)
                if not title or title.lower() in ignore_titles:
                    continue
                if "~" in raw_href or "gateway" in raw_href:
                    continue
                if "/portal/site/" in raw_href:
                    full_url = urljoin(base_url, raw_href)
                    if not any(c["url"] == full_url for c in courses):
                        courses.append({"title": title, "url": full_url})

            print(f"[DEBUG DEADLINES] Найдено курсов: {len(courses)}")

            # 2. Обход курсов
            for c in courses:
                try:
                    async with session.get(c["url"], proxy=PORTAL_PROXY) as resp:
                        site_html = await resp.text()
                except Exception:
                    continue

                course_soup = BeautifulSoup(site_html, "html.parser")
                tool_links = {}
                for a in course_soup.find_all("a", href=True):
                    text = a.get_text(strip=True).lower()
                    href = urljoin(base_url, a["href"].strip())
                    if "задания" in text and "/portal/site/" in href:
                        tool_links["tasks"] = href
                    elif ("тесты" in text or "экзамен" in text) and "/portal/site/" in href:
                        tool_links["tests"] = href

                for tool_type, tool_url in tool_links.items():
                    try:
                        async with session.get(tool_url, proxy=PORTAL_PROXY) as resp:
                            p_html = await resp.text()

                        p_soup = BeautifulSoup(p_html, "html.parser")
                        iframe = p_soup.find("iframe")
                        if not iframe or not iframe.get("src"):
                            continue

                        iframe_url = urljoin(base_url, iframe["src"].strip())
                        async with session.get(iframe_url, proxy=PORTAL_PROXY) as resp:
                            frame_html = await resp.text()

                        f_soup = BeautifulSoup(frame_html, "html.parser")

                        # Парсинг заданий
                        if tool_type == "tasks":
                            for tr in f_soup.find_all("tr"):
                                cols = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
                                if len(cols) >= 4:
                                    # 1. Достаем название из ссылки (так как колонка 0 часто пустая)
                                    a_tag = tr.find("a")
                                    title_t = a_tag.get_text(strip=True) if a_tag else cols[1]

                                    if not title_t or title_t.lower() in ["название приложения", "название", "статус"]:
                                        continue

                                    # 2. Проверяем, не сдана ли работа
                                    row_text = " ".join(cols).lower()
                                    if "сдано" in row_text or "проверено" in row_text or "возвращено" in row_text:
                                        continue

                                    # 3. Ищем саму дату дедлайна среди всех колонок
                                    parsed_dates = []
                                    for col in cols:
                                        parsed = parse_deadline(col)
                                        if parsed:
                                            parsed_dates.append((parsed, col))

                                    if parsed_dates:
                                        dt, deadline_raw = parsed_dates[-1]  # Последняя дата - это Срок сдачи
                                    else:
                                        dt = None

                                    # 4. Сохраняем, только если дедлайн ещё не прошел
                                    if dt and dt > now:
                                        results.append({
                                            "id": f"task_{title_t}_{deadline_raw}",
                                            "course": c["title"],
                                            "type": "Задание",
                                            "title": title_t,
                                            "deadline_iso": dt.isoformat(),
                                            "deadline_str": deadline_raw,
                                            "timestamp": dt.timestamp()
                                        })

                        # Парсинг тестов
                        elif tool_type == "tests":
                            for tr in f_soup.find_all("tr"):
                                cols = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
                                if len(cols) >= 3:
                                    title_t, deadline_raw = cols[0], cols[2]

                                    # Фильтр мусора и заголовков
                                    if not title_t or title_t.lower() in ["название", "пройти тест"]:
                                        continue

                                    dt = parse_deadline(deadline_raw)
                                    # Если даты нет или она уже прошла - пропускаем
                                    if dt and dt > now:
                                        results.append({
                                            "id": f"test_{title_t}_{deadline_raw}",
                                            "course": c["title"],
                                            "type": "Тест",
                                            "title": title_t,
                                            "deadline_iso": dt.isoformat(),
                                            "deadline_str": deadline_raw,
                                            "timestamp": dt.timestamp()
                                        })
                    except Exception:
                        pass

    except Exception as e:
        print(f"[ERROR при сборе дедлайнов]: {type(e).__name__}: {e}")
        traceback.print_exc()

    # Сортируем результаты по дате сдачи (сначала самые горящие)
    results.sort(key=lambda x: x["timestamp"])

    print(f"[DEBUG TOTAL RESULTS]: Собрано актуальных дедлайнов: {len(results)}")
    return results