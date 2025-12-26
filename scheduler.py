"""Планировщик уведомлений"""
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from datetime import datetime, timedelta
from sqlalchemy import select, and_
from database import async_session, CalendarEvent, Notification
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
    async with async_session() as session:
        for minutes_before in Config.NOTIFICATION_TIMES:
            notification_time = start_datetime - timedelta(minutes=minutes_before)
            
            # Создаем уведомление только если время еще не прошло
            if notification_time > datetime.now(notification_time.tzinfo):
                notification = Notification(
                    event_id=event_id,
                    notification_time=notification_time
                )
                session.add(notification)
        
        await session.commit()
        logger.info(f"Созданы уведомления для события {event_id}")


async def check_and_send_notifications(bot: Bot):
    """Проверка и отправка уведомлений"""
    try:
        timezone = pytz.timezone(Config.TIMEZONE)
        async with async_session() as session:
            # Находим уведомления, которые нужно отправить (в течение следующих 2 минут)
            now = datetime.now(timezone)
            check_time = now + timedelta(minutes=2)
            
            stmt = select(Notification).join(CalendarEvent).where(
                and_(
                    Notification.sent == False,
                    Notification.notification_time <= check_time,
                    Notification.notification_time >= now - timedelta(minutes=1)
                )
            )
            
            result = await session.execute(stmt)
            notifications = result.scalars().all()
            
            for notification in notifications:
                # Получаем событие
                stmt_event = select(CalendarEvent).where(
                    CalendarEvent.id == notification.event_id
                )
                result_event = await session.execute(stmt_event)
                event = result_event.scalar_one_or_none()
                
                if not event:
                    continue
                
                # Отправляем уведомление
                event_time = event.start_datetime
                if event_time.tzinfo is None:
                    event_time = timezone.localize(event_time)
                time_until = event_time - now
                minutes_until = max(0, int(time_until.total_seconds() / 60))
                
                message_text = (
                    f"🔔 Напоминание!\n\n"
                    f"📌 {event.summary}\n"
                    f"📅 {event.start_datetime.strftime('%d.%m.%Y в %H:%M')}\n"
                    f"⏰ Через {minutes_until} минут"
                )
                
                try:
                    await bot.send_message(
                        chat_id=event.telegram_user_id,
                        text=message_text
                    )
                    
                    # Помечаем уведомление как отправленное
                    notification.sent = True
                    await session.commit()
                    
                    logger.info(f"Отправлено уведомление для события {event.summary}")
                    
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
