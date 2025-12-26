"""Конфигурация приложения"""
import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    """Настройки приложения"""
    
    # Telegram
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    
    # Yandex SpeechKit (для транскрибации голоса)
    YANDEX_SPEECHKIT_API_KEY = os.getenv("YANDEX_SPEECHKIT_API_KEY")
    YANDEX_SPEECHKIT_FOLDER_ID = os.getenv("YANDEX_SPEECHKIT_FOLDER_ID")
    
    # Google Gemini (для обработки текста и извлечения данных о событиях)
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
    
    # Яндекс Календарь (опционально - можно настроить через бота командой /setup)
    # Эти переменные используются только для обратной совместимости, если пользователь не настроил свои учетные данные
    YANDEX_USER = os.getenv("YANDEX_USER")
    YANDEX_PASS = os.getenv("YANDEX_PASS")
    CALDAV_URL = f"https://caldav.yandex.ru/calendars/{os.getenv('YANDEX_USER', '')}/"
    
    # Уведомления
    NOTIFICATION_TIMES = [int(x) for x in os.getenv("NOTIFICATION_TIMES", "15,60").split(",")]
    
    # Часовой пояс
    TIMEZONE = os.getenv("TIMEZONE", "Europe/Moscow")
    
    # База данных
    @staticmethod
    def _normalize_database_url(url: str) -> str:
        """Нормализация URL базы данных для использования правильного драйвера"""
        # Если это PostgreSQL URL без указания драйвера, используем asyncpg
        if url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        # Если это PostgreSQL с psycopg2, заменяем на asyncpg
        elif url.startswith("postgresql+psycopg2://"):
            url = url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
        return url
    
    _raw_db_url = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./calendar_bot.db")
    DATABASE_URL = _normalize_database_url(_raw_db_url)
    
    @classmethod
    def validate(cls):
        """Проверка наличия обязательных переменных"""
        required = [
            "TELEGRAM_BOT_TOKEN",
            "YANDEX_SPEECHKIT_API_KEY",  # Для Yandex SpeechKit
            "YANDEX_SPEECHKIT_FOLDER_ID",  # Для Yandex SpeechKit
            "GEMINI_API_KEY",  # Для обработки текста
            # YANDEX_USER и YANDEX_PASS опциональны - можно настроить через бота командой /setup
        ]
        missing = [var for var in required if not getattr(cls, var)]
        if missing:
            raise ValueError(f"Отсутствуют обязательные переменные окружения: {', '.join(missing)}")
