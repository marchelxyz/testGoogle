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
    DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./calendar_bot.db")
    
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
