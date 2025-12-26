"""Сервис для работы с Яндекс Календарем через CalDAV"""
import caldav
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from config import Config
import logging
import pytz

logger = logging.getLogger(__name__)

class YandexCalendarService:
    """Сервис для работы с Яндекс Календарем"""
    
    def __init__(self, yandex_user: Optional[str] = None, yandex_password: Optional[str] = None):
        """
        Инициализация сервиса календаря
        
        Args:
            yandex_user: Email пользователя Яндекс (если None, используется из Config)
            yandex_password: Пароль приложения Яндекс (если None, используется из Config)
        """
        self.yandex_user = yandex_user or Config.YANDEX_USER
        self.yandex_password = yandex_password or Config.YANDEX_PASS
        self.client: Optional[caldav.DAVClient] = None
        self.calendar: Optional[caldav.Calendar] = None
        if self.yandex_user and self.yandex_password:
            self._connect()
    
    def _connect(self):
        """Подключение к Яндекс Календарю"""
        try:
            if not self.yandex_user or not self.yandex_password:
                raise ValueError("Не указаны учетные данные Яндекс Календаря")
            
            caldav_url = f"https://caldav.yandex.ru/calendars/{self.yandex_user}/"
            
            self.client = caldav.DAVClient(
                url=caldav_url,
                username=self.yandex_user,
                password=self.yandex_password
            )
            
            # Получаем главный календарь
            principal = self.client.principal()
            calendars = principal.calendars()
            
            if not calendars:
                raise ValueError("Календари не найдены в Яндекс аккаунте")
            
            # Берем первый календарь (обычно основной)
            self.calendar = calendars[0]
            logger.info(f"Подключено к календарю: {self.calendar.name}")
            
        except Exception as e:
            logger.error(f"Ошибка подключения к Яндекс Календарю: {e}")
            raise ValueError(f"Не удалось подключиться к Яндекс Календарю: {e}")
    
    def reconnect(self, yandex_user: str, yandex_password: str):
        """Переподключение с новыми учетными данными"""
        self.yandex_user = yandex_user
        self.yandex_password = yandex_password
        self.client = None
        self.calendar = None
        self._connect()
    
    def create_event(
        self,
        summary: str,
        start_datetime: datetime,
        duration_minutes: int = 60,
        description: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Создание события в календаре
        
        Args:
            summary: Название события
            start_datetime: Дата и время начала
            duration_minutes: Длительность в минутах
            description: Описание события
            
        Returns:
            Словарь с информацией о созданном событии
        """
        try:
            if not self.calendar:
                if not self.yandex_user or not self.yandex_password:
                    raise ValueError("Учетные данные не настроены. Используйте команду /setup для настройки.")
                self._connect()
            
            # Обрабатываем часовой пояс для start_datetime
            timezone = pytz.timezone(Config.TIMEZONE)
            if start_datetime.tzinfo is None:
                # Если datetime без часового пояса, считаем что это локальное время
                start_datetime = timezone.localize(start_datetime)
            elif start_datetime.tzinfo != timezone:
                # Если часовой пояс другой, конвертируем в локальный
                start_datetime = start_datetime.astimezone(timezone)
            
            # Вычисляем конец события
            end_datetime = start_datetime + timedelta(minutes=duration_minutes)
            
            # CalDAV обычно работает с naive datetime (без часового пояса) или UTC
            # Конвертируем в UTC для CalDAV, но сохраняем оригинальные значения для возврата
            start_datetime_utc = start_datetime.astimezone(pytz.UTC)
            end_datetime_utc = end_datetime.astimezone(pytz.UTC)
            
            # Создаем событие (CalDAV может работать с UTC datetime)
            event = self.calendar.save_event(
                dtstart=start_datetime_utc,
                dtend=end_datetime_utc,
                summary=summary,
                description=description or "Создано через Telegram Бота"
            )
            
            logger.info(f"Событие '{summary}' успешно создано в календаре")
            
            return {
                "event_id": event.url.split("/")[-1].replace(".ics", ""),
                "url": event.url,
                "summary": summary,
                "start": start_datetime,
                "end": end_datetime
            }
            
        except Exception as e:
            logger.error(f"Ошибка создания события: {e}")
            raise
    
    def get_events(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None
    ) -> list:
        """
        Получение событий из календаря
        
        Args:
            start_date: Начало периода
            end_date: Конец периода
            
        Returns:
            Список событий
        """
        try:
            if not self.calendar:
                if not self.yandex_user or not self.yandex_password:
                    raise ValueError("Учетные данные не настроены. Используйте команду /setup для настройки.")
                self._connect()
            
            # Обрабатываем часовые пояса для дат поиска
            timezone = pytz.timezone(Config.TIMEZONE)
            if start_date:
                if start_date.tzinfo is None:
                    start_date = timezone.localize(start_date)
                elif start_date.tzinfo != timezone:
                    start_date = start_date.astimezone(timezone)
                # Конвертируем в UTC для CalDAV
                start_date = start_date.astimezone(pytz.UTC)
            
            if end_date:
                if end_date.tzinfo is None:
                    end_date = timezone.localize(end_date)
                elif end_date.tzinfo != timezone:
                    end_date = end_date.astimezone(timezone)
                # Конвертируем в UTC для CalDAV
                end_date = end_date.astimezone(pytz.UTC)
            
            if start_date and end_date:
                events = self.calendar.search(
                    start=start_date,
                    end=end_date
                )
            else:
                events = self.calendar.events()
            
            return list(events)
            
        except Exception as e:
            logger.error(f"Ошибка получения событий: {e}")
            return []
