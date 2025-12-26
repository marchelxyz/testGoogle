"""Планировщик уведомлений"""
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from datetime import datetime, timedelta
from database import create_notification as db_create_notification, get_pending_notifications, mark_notification_sent
from config import Config
import logging
import pytz
from aiogram import Bot

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler(timezone=Config.TIMEZONE)


async def create_notifications(event_id: int, start_datetime: datetime):
    """
    Создание уведомлений для события
    
    Args:
        event_id: ID события в базе данных
        start_datetime: Дата и время начала события
    """
    for minutes_before in Config.NOTIFICATION_TIMES:
        notification_time = start_datetime - timedelta(minutes=minutes_before)
        
        # Создаем уведомление только если время еще не прошло
        if notification_time > datetime.now(notification_time.tzinfo):
            await db_create_notification(event_id, notification_time)
    
    logger.info(f"Созданы уведомления для события {event_id}")


async def check_and_send_notifications(bot: Bot):
    """Проверка и отправка уведомлений"""
    try:
        timezone = pytz.timezone(Config.TIMEZONE)
        # Находим уведомления, которые нужно отправить (в течение следующих 2 минут)
        now = datetime.now(timezone)
        check_time = now + timedelta(minutes=2)
        
        notifications = await get_pending_notifications(check_time, now)
        
        for notification in notifications:
            # Данные события уже включены в результат запроса
            event_summary = notification['summary']
            telegram_user_id = notification['telegram_user_id']
            event_start = notification['start_datetime']
            
            # Отправляем уведомление
            if isinstance(event_start, datetime):
                event_time = event_start
            else:
                # Если это строка, парсим её
                from dateutil import parser
                event_time = parser.parse(str(event_start))
            
            if event_time.tzinfo is None:
                event_time = timezone.localize(event_time)
            time_until = event_time - now
            minutes_until = max(0, int(time_until.total_seconds() / 60))
            
            message_text = (
                f"🔔 Напоминание!\n\n"
                f"📌 {event_summary}\n"
                f"📅 {event_time.strftime('%d.%m.%Y в %H:%M')}\n"
                f"⏰ Через {minutes_until} минут"
            )
            
            try:
                await bot.send_message(
                    chat_id=telegram_user_id,
                    text=message_text
                )
                
                # Помечаем уведомление как отправленное
                await mark_notification_sent(notification['id'])
                
                logger.info(f"Отправлено уведомление для события {event_summary}")
                
            except Exception as e:
                logger.error(f"Ошибка отправки уведомления: {e}")
                    
    except Exception as e:
        logger.error(f"Ошибка проверки уведомлений: {e}")


def start_scheduler(bot: Bot):
    """Запуск планировщика"""
    # Проверяем уведомления каждую минуту
    scheduler.add_job(
        check_and_send_notifications,
        trigger=IntervalTrigger(minutes=1),
        args=[bot],
        id="check_notifications",
        replace_existing=True
    )
    
    scheduler.start()
    logger.info("Планировщик уведомлений запущен")
