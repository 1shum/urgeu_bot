import os
import json
import asyncpg

DATABASE_URL = os.getenv("DATABASE_URL")

# Локальный кэш, чтобы не дергать базу на каждое чтение
users_cache = {}
_pool = None


async def init_db():
    global _pool, users_cache
    if not DATABASE_URL:
        print("[DB] Внимание: DATABASE_URL не задан. Работаем в памяти/локально.")
        return

    try:
        # Подключаемся к Neon
        # Очищаем URL от параметров запроса и передаем ssl="require"
        clean_url = DATABASE_URL.split("?")[0]
        _pool = await asyncpg.create_pool(clean_url, ssl="require", min_size=1, max_size=5)

        async with _pool.acquire() as conn:
            # Создаем таблицу, если её нет
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id TEXT PRIMARY KEY,
                    data JSONB NOT NULL
                );
            """)

            # Загружаем всех пользователей в кэш
            rows = await conn.fetch("SELECT user_id, data FROM users;")
            for r in rows:
                users_cache[str(r["user_id"])] = json.loads(r["data"]) if isinstance(r["data"], str) else dict(
                    r["data"])

        print(f"[DB] Успешно подключено к Neon! Загружено пользователей: {len(users_cache)}")
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