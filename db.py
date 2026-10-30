import os
import json
import asyncpg

DATABASE_URL = os.getenv("DATABASE_URL")

# Локальный кэш, чтобы не дергать базу на каждое чтение
users_cache = {}
notifs_cache = {}
_pool = None


async def init_db():
    global _pool
    try:
        # ВОТ ЭТА СТРОКА ОЧЕНЬ ВАЖНА - она создает подключение к Neon
        _pool = await asyncpg.create_pool(DATABASE_URL, ssl="require", min_size=1, max_size=5)

        async with _pool.acquire() as conn:
            # --- 1. ТАБЛИЦА ПОЛЬЗОВАТЕЛЕЙ ---
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id TEXT PRIMARY KEY,
                    data JSONB NOT NULL
                );
            """)

            rows = await conn.fetch("SELECT user_id, data FROM users;")
            for r in rows:
                users_cache[str(r["user_id"])] = json.loads(r["data"]) if isinstance(r["data"], str) else dict(r["data"])

            print(f"[DB] Успешно подключено к Neon! Загружено пользователей: {len(users_cache)}")

            # --- 2. ТАБЛИЦА УВЕДОМЛЕНИЙ ---
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS notifications (
                    user_id TEXT PRIMARY KEY,
                    data JSONB NOT NULL
                );
            """)

            rows_notifs = await conn.fetch("SELECT user_id, data FROM notifications;")
            for r in rows_notifs:
                notifs_cache[str(r["user_id"])] = json.loads(r["data"]) if isinstance(r["data"], str) else dict(r["data"])

            print(f"[DB] Загружено настроек уведомлений: {len(notifs_cache)}")

    except Exception as e:
        print(f"[DB ERROR] Ошибка подключения к базе: {e}")


async def save_user_db(user_id: str, user_dict: dict):
    """Сохраняет пользователя в кэш и в Neon"""
    user_id = str(user_id)
    users_cache[user_id] = user_dict

    if not _pool:
        return

    try:
        async with _pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO users (user_id, data)
                VALUES ($1, $2::jsonb)
                ON CONFLICT (user_id) 
                DO UPDATE SET data = $2::jsonb;
            """, user_id, json.dumps(user_dict, ensure_ascii=False))
    except Exception as e:
        print(f"[DB ERROR] Не удалось сохранить пользователя {user_id}: {e}")


def get_users_cache():
    return users_cache


def get_notifs_cache() -> dict:
    return notifs_cache


async def save_notif_db(user_id: str, notif_dict: dict):
    """Сохраняет настройки уведомлений в кэш и в Neon"""
    user_id = str(user_id)
    notifs_cache[user_id] = notif_dict

    if not _pool:
        return

    try:
        async with _pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO notifications (user_id, data)
                VALUES ($1, $2::jsonb)
                ON CONFLICT (user_id) 
                DO UPDATE SET data = $2::jsonb;
            """, user_id, json.dumps(notif_dict, ensure_ascii=False))
    except Exception as e:
        print(f"[DB ERROR] Ошибка сохранения уведомлений для {user_id}: {e}")