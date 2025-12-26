"""Telegram бот для работы с календарем"""
import os
import logging
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message
from aiogram.filters import Command
from aiogram.fsm.storage.memory import MemoryStorage
from aiofiles import os as aiofiles_os
import aiofiles

from config import Config
from transcription import TranscriptionService
from nlu_service import NLUService
from calendar_service import YandexCalendarService
from database import CalendarEvent, Notification, init_db, async_session
from sqlalchemy import select
from datetime import datetime

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Инициализация бота и диспетчера
bot = Bot(token=Config.TELEGRAM_BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# Инициализация сервисов (ленивая инициализация для календаря)
transcription_service = TranscriptionService()
nlu_service = NLUService()
calendar_service = None

def get_calendar_service():
    """Получение сервиса календаря с ленивой инициализацией"""
    global calendar_service
    if calendar_service is None:
        calendar_service = YandexCalendarService()
    return calendar_service

# Создаем папку для временных файлов
TEMP_DIR = "temp"
os.makedirs(TEMP_DIR, exist_ok=True)


@dp.message(Command("start"))
async def cmd_start(message: Message):
    """Обработчик команды /start"""
    await message.answer(
        "👋 Привет! Я бот для управления календарем.\n\n"
        "Отправь мне голосовое сообщение с описанием события, и я добавлю его в твой Яндекс Календарь.\n\n"
        "Примеры:\n"
        "• \"Поставь встречу с клиентом на завтра в 15:00\"\n"
        "• \"Созвон с командой послезавтра в 10 утра на час\"\n"
        "• \"Напомни про презентацию через 2 дня в 14:30\"\n\n"
        "Используй /help для получения справки."
    )


@dp.message(Command("help"))
async def cmd_help(message: Message):
    """Обработчик команды /help"""
    await message.answer(
        "📋 Справка по использованию бота:\n\n"
        "1. Отправь голосовое сообщение с описанием события\n"
        "2. Бот распознает речь и создаст событие в календаре\n"
        "3. Ты получишь уведомления за 60 и 15 минут до события\n\n"
        "Поддерживаемые форматы:\n"
        "• \"Завтра в 15:00\"\n"
        "• \"Послезавтра в 10 утра\"\n"
        "• \"Через 3 дня в 14:30\"\n"
        "• \"Сегодня в 18:00\"\n\n"
        "Команды:\n"
        "/start - Начать работу\n"
        "/help - Показать эту справку\n"
        "/list - Показать ближайшие события"
    )


@dp.message(Command("list"))
async def cmd_list(message: Message):
    """Показать ближайшие события"""
    try:
        # Получаем события на ближайшие 7 дней
        from datetime import timedelta
        start_date = datetime.now()
        end_date = start_date + timedelta(days=7)
        
        cal_service = get_calendar_service()
        events = cal_service.get_events(start_date, end_date)
        
        if not events:
            await message.answer("📅 У тебя нет событий на ближайшие 7 дней.")
            return
        
        text = "📅 Ближайшие события:\n\n"
        for i, event in enumerate(events[:10], 1):  # Показываем максимум 10
            try:
                event_data = event.icalendar_component
                summary = str(event_data.get('summary', 'Без названия'))
                dtstart = event_data.get('dtstart')
                if dtstart:
                    dt = dtstart.dt
                    if hasattr(dt, 'strftime'):
                        time_str = dt.strftime("%d.%m.%Y %H:%M")
                    else:
                        time_str = str(dt)
                else:
                    time_str = "Время не указано"
                
                text += f"{i}. {summary}\n   📅 {time_str}\n\n"
            except Exception as e:
                logger.error(f"Ошибка обработки события: {e}")
                continue
        
        await message.answer(text)
        
    except Exception as e:
        logger.error(f"Ошибка получения списка событий: {e}")
        await message.answer("❌ Произошла ошибка при получении списка событий.")


@dp.message(F.voice)
async def handle_voice(message: Message):
    """Обработчик голосовых сообщений"""
    await message.answer("🎤 Обрабатываю голосовое сообщение...")
    
    try:
        # Скачиваем голосовое сообщение
        voice_file = await bot.get_file(message.voice.file_id)
        file_path = os.path.join(TEMP_DIR, f"{message.voice.file_id}.ogg")
        
        await bot.download_file(voice_file.file_path, file_path)
        logger.info(f"Голосовое сообщение скачано: {file_path}")
        
        # Транскрибируем голос в текст
        await message.answer("🔤 Распознаю речь...")
        text = await transcription_service.transcribe_voice(file_path)
        
        if not text or len(text.strip()) == 0:
            await message.answer("❌ Не удалось распознать речь. Попробуйте записать сообщение еще раз.")
            return
        
        await message.answer(f"📝 Распознанный текст: \"{text}\"")
        
        # Обрабатываем текст через NLU
        await message.answer("🤖 Анализирую запрос...")
        event_info = await nlu_service.extract_event_info(text)
        
        # Создаем событие в календаре
        if event_info["action"] == "create_event":
            await message.answer("📅 Создаю событие в календаре...")
            
            cal_service = get_calendar_service()
            event_data = cal_service.create_event(
                summary=event_info["summary"],
                start_datetime=event_info["start_datetime"],
                duration_minutes=event_info.get("duration_minutes", 60),
                description=event_info.get("description")
            )
            
            # Сохраняем событие в базу данных
            async with async_session() as session:
                db_event = CalendarEvent(
                    event_id=event_data["event_id"],
                    summary=event_data["summary"],
                    description=event_info.get("description"),
                    start_datetime=event_data["start"],
                    end_datetime=event_data["end"],
                    telegram_user_id=message.from_user.id
                )
                session.add(db_event)
                await session.commit()
                await session.refresh(db_event)
                
                # Создаем уведомления
                from scheduler import create_notifications
                await create_notifications(db_event.id, event_data["start"])
            
            start_str = event_data["start"].strftime("%d.%m.%Y в %H:%M")
            await message.answer(
                f"✅ Событие успешно создано!\n\n"
                f"📌 {event_data['summary']}\n"
                f"📅 {start_str}\n"
                f"⏱ Длительность: {event_info.get('duration_minutes', 60)} минут\n\n"
                f"Я напомню тебе за 60 и 15 минут до начала."
            )
        else:
            await message.answer(f"⚠️ Действие '{event_info['action']}' пока не поддерживается.")
        
        # Удаляем временный файл
        try:
            await aiofiles_os.remove(file_path)
        except:
            pass
            
    except Exception as e:
        logger.error(f"Ошибка обработки голосового сообщения: {e}")
        await message.answer(
            f"❌ Произошла ошибка: {str(e)}\n\n"
            "Попробуйте записать сообщение еще раз или используйте /help для справки."
        )


@dp.message()
async def handle_text(message: Message):
    """Обработчик текстовых сообщений"""
    await message.answer(
        "📝 Я работаю только с голосовыми сообщениями.\n\n"
        "Отправь мне голосовое сообщение с описанием события, и я добавлю его в календарь.\n\n"
        "Используй /help для получения справки."
    )


async def main():
    """Главная функция запуска бота"""
    # Проверяем конфигурацию
    try:
        Config.validate()
    except ValueError as e:
        logger.error(f"Ошибка конфигурации: {e}")
        logger.error("Проверьте файл .env и убедитесь, что все переменные заполнены")
        return
    
    # Инициализируем базу данных
    try:
        await init_db()
        logger.info("База данных инициализирована")
    except Exception as e:
        logger.error(f"Ошибка инициализации базы данных: {e}")
        return
    
    # Проверяем подключение к календарю (ленивая инициализация, но лучше проверить сразу)
    try:
        get_calendar_service()
        logger.info("Подключение к Яндекс Календарю успешно")
    except Exception as e:
        logger.warning(f"Не удалось подключиться к Яндекс Календарю: {e}")
        logger.warning("Бот будет работать, но создание событий может не работать")
    
    # Запускаем планировщик уведомлений
    try:
        from scheduler import start_scheduler
        start_scheduler(bot)
        logger.info("Планировщик уведомлений запущен")
    except Exception as e:
        logger.error(f"Ошибка запуска планировщика: {e}")
        logger.warning("Бот будет работать, но уведомления могут не отправляться")
    
    # Запускаем бота
    logger.info("Бот запущен и готов к работе")
    try:
        await dp.start_polling(bot)
    except Exception as e:
        logger.error(f"Критическая ошибка бота: {e}")
        raise


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
