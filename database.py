"""База данных для хранения событий и уведомлений"""
import asyncpg
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from config import Config
import logging

logger = logging.getLogger(__name__)

# Глобальный пул соединений
_pool: Optional[asyncpg.Pool] = None


async def get_pool() -> asyncpg.Pool:
    """Получение пула соединений"""
    global _pool
    if _pool is None:
        # Парсим DATABASE_URL для получения параметров подключения
        db_url = Config.DATABASE_URL
        if not db_url:
            raise ValueError("DATABASE_URL не указан")
        
        # Убираем префиксы для asyncpg
        if db_url.startswith("postgresql+asyncpg://"):
            db_url = db_url.replace("postgresql+asyncpg://", "postgresql://", 1)
        elif db_url.startswith("postgresql://"):
            pass  # Уже правильный формат
        else:
            raise ValueError(f"Неподдерживаемый формат DATABASE_URL: {db_url}")
        
        # Парсим URL (urlparse правильно обрабатывает специальные символы в пароле)
        from urllib.parse import urlparse, unquote
        parsed = urlparse(db_url)
        
        # Декодируем пароль, если он был закодирован
        password = unquote(parsed.password) if parsed.password else None
        
        _pool = await asyncpg.create_pool(
            host=parsed.hostname,
            port=parsed.port or 5432,
            user=unquote(parsed.username) if parsed.username else None,
            password=password,
            database=unquote(parsed.path.lstrip('/')) if parsed.path else None,
            min_size=1,
            max_size=10
        )
    return _pool


async def init_db():
    """Инициализация базы данных - создание таблиц"""
    pool = await get_pool()
    
    async with pool.acquire() as conn:
        # Создаем таблицу calendar_events
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS calendar_events (
                id SERIAL PRIMARY KEY,
                event_id VARCHAR(255) UNIQUE NOT NULL,
                summary VARCHAR(255) NOT NULL,
                description TEXT,
                start_datetime TIMESTAMP NOT NULL,
                end_datetime TIMESTAMP NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                notified_15min BOOLEAN DEFAULT FALSE,
                notified_60min BOOLEAN DEFAULT FALSE,
                telegram_user_id INTEGER NOT NULL
            )
        """)
        
        # Создаем таблицу notifications
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS notifications (
                id SERIAL PRIMARY KEY,
                event_id INTEGER NOT NULL,
                notification_time TIMESTAMP NOT NULL,
                sent BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Создаем таблицу user_credentials
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS user_credentials (
                id SERIAL PRIMARY KEY,
                telegram_user_id INTEGER UNIQUE NOT NULL,
                yandex_user VARCHAR(255) NOT NULL,
                yandex_password VARCHAR(255) NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        logger.info("Таблицы базы данных созданы/проверены")


# Функции для работы с CalendarEvent
async def create_calendar_event(
    event_id: str,
    summary: str,
    start_datetime: datetime,
    end_datetime: datetime,
    telegram_user_id: int,
    description: Optional[str] = None
) -> int:
    """Создание события календаря"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO calendar_events 
            (event_id, summary, description, start_datetime, end_datetime, telegram_user_id)
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING id
        """, event_id, summary, description, start_datetime, end_datetime, telegram_user_id)
        return row['id']


async def get_calendar_event_by_id(event_id: int) -> Optional[Dict[str, Any]]:
    """Получение события по ID"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT * FROM calendar_events WHERE id = $1
        """, event_id)
        return dict(row) if row else None


async def get_calendar_event_by_event_id(event_id: str) -> Optional[Dict[str, Any]]:
    """Получение события по event_id (ID из Яндекс Календаря)"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT * FROM calendar_events WHERE event_id = $1
        """, event_id)
        return dict(row) if row else None


# Функции для работы с Notification
async def create_notification(event_id: int, notification_time: datetime) -> int:
    """Создание уведомления"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO notifications (event_id, notification_time)
            VALUES ($1, $2)
            RETURNING id
        """, event_id, notification_time)
        return row['id']


async def get_pending_notifications(
    check_time: datetime,
    now: datetime
) -> List[Dict[str, Any]]:
    """Получение уведомлений, которые нужно отправить"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT n.*, ce.summary, ce.telegram_user_id, ce.start_datetime
            FROM notifications n
            INNER JOIN calendar_events ce ON n.event_id = ce.id
            WHERE n.sent = FALSE
            AND n.notification_time <= $1
            AND n.notification_time >= $2
        """, check_time, now - timedelta(minutes=1))
        return [dict(row) for row in rows]


async def mark_notification_sent(notification_id: int):
    """Пометить уведомление как отправленное"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            UPDATE notifications SET sent = TRUE WHERE id = $1
        """, notification_id)


# Функции для работы с UserCredentials
async def get_user_credentials(telegram_user_id: int) -> Optional[Dict[str, Any]]:
    """Получение учетных данных пользователя"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT * FROM user_credentials WHERE telegram_user_id = $1
        """, telegram_user_id)
        return dict(row) if row else None


async def save_user_credentials(
    telegram_user_id: int,
    yandex_user: str,
    yandex_password: str
):
    """Сохранение или обновление учетных данных пользователя"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        existing = await get_user_credentials(telegram_user_id)
        if existing:
            await conn.execute("""
                UPDATE user_credentials
                SET yandex_user = $1, yandex_password = $2, updated_at = CURRENT_TIMESTAMP
                WHERE telegram_user_id = $3
            """, yandex_user, yandex_password, telegram_user_id)
        else:
            await conn.execute("""
                INSERT INTO user_credentials (telegram_user_id, yandex_user, yandex_password)
                VALUES ($1, $2, $3)
            """,                 telegram_user_id, yandex_user, yandex_password)
