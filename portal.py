import asyncio
import html as html_lib
import os
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
    и собирает лаконичный список дисциплин и процентов.
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

            # 2. Переходим на главную страницу студента, чтобы узнать его личный URL страницы обучения
            home_url = f"https://portal.usue.ru/portal/site/~{login}"
            async with session.get(home_url, allow_redirects=True, proxy=PORTAL_PROXY) as resp:
                if resp.status != 200:
                    return False, f"Ошибка загрузки личного кабинета: {resp.status}", {}
                raw_home = await resp.read()
                soup_home = BeautifulSoup(raw_home.decode("utf-8", errors="ignore"), "html.parser")

            # Ищем пункт меню "Сведения об обучении" и забираем ссылку из него
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

            # Загружаем найденную страницу с оценками
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

            # Извлечение актуальной AJAX-ссылки вкладки Рейтинг
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

            async with session.get(ajax_url, headers=ajax_headers, allow_redirects=True,proxy=PORTAL_PROXY) as ajax_resp:
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