"""Сервис для работы с Яндекс Календарем через CalDAV"""
import caldav
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from config import Config
import logging

logger = logging.getLogger(__name__)

class YandexCalendarService:
    """Сервис для работы с Яндекс Календарем"""
    
    def __init__(self):
        self.client: Optional[caldav.DAVClient] = None
        self.calendar: Optional[caldav.Calendar] = None
        self._connect()
    
    def _connect(self):
        """Подключение к Яндекс Календарю"""
        try:
            if not Config.YANDEX_USER or not Config.YANDEX_PASS:
                raise ValueError("Не указаны учетные данные Яндекс Календаря")
            
            self.client = caldav.DAVClient(
                url=Config.CALDAV_URL,
                username=Config.YANDEX_USER,
                password=Config.YANDEX_PASS
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
                self._connect()
            
            # Вычисляем конец события
            end_datetime = start_datetime + timedelta(minutes=duration_minutes)
            
            # Создаем событие
            event = self.calendar.save_event(
                dtstart=start_datetime,
                dtend=end_datetime,
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
                self._connect()
            
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
