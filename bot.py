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
from database import (
    init_db, 
    get_user_credentials, 
    save_user_credentials as db_save_user_credentials,
    create_calendar_event,
    get_calendar_event_by_id
)
from datetime import datetime, timedelta
import re
import pytz

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
# Словарь для хранения сервисов календаря для каждого пользователя
user_calendar_services = {}

async def get_user_calendar_service(telegram_user_id: int) -> YandexCalendarService:
    """Получение сервиса календаря для конкретного пользователя"""
    if telegram_user_id not in user_calendar_services:
        # Пытаемся получить учетные данные из БД
        credentials = await get_user_credentials(telegram_user_id)
            
        if credentials:
            # Создаем сервис с учетными данными пользователя
            user_calendar_services[telegram_user_id] = YandexCalendarService(
                yandex_user=credentials['yandex_user'],
                yandex_password=credentials['yandex_password']
            )
        else:
            # Используем глобальные учетные данные из Config (для обратной совместимости)
            if Config.YANDEX_USER and Config.YANDEX_PASS:
                user_calendar_services[telegram_user_id] = YandexCalendarService()
            else:
                raise ValueError("Учетные данные не настроены. Используйте команду /setup для настройки.")
    
    return user_calendar_services[telegram_user_id]

async def save_user_credentials(telegram_user_id: int, yandex_user: str, yandex_password: str):
    """Сохранение учетных данных пользователя"""
    await db_save_user_credentials(telegram_user_id, yandex_user, yandex_password)
    
    # Обновляем сервис календаря для пользователя
    if telegram_user_id in user_calendar_services:
        user_calendar_services[telegram_user_id].reconnect(yandex_user, yandex_password)
    else:
        user_calendar_services[telegram_user_id] = YandexCalendarService(
            yandex_user=yandex_user,
            yandex_password=yandex_password
        )

# Создаем папку для временных файлов
TEMP_DIR = "temp"
os.makedirs(TEMP_DIR, exist_ok=True)


@dp.message(Command("start"))
async def cmd_start(message: Message):
    """Обработчик команды /start"""
    # Проверяем, настроены ли учетные данные пользователя
    credentials = await get_user_credentials(message.from_user.id)
    
    welcome_text = (
        "👋 Привет! Я бот для управления календарем.\n\n"
    )
    
    if not credentials:
        welcome_text += (
            "⚠️ Сначала нужно настроить учетные данные Яндекс Календаря.\n"
            "Используй команду /setup для настройки.\n\n"
        )
    else:
        welcome_text += (
            f"✅ Учетные данные настроены: {credentials['yandex_user']}\n\n"
        )
    
    welcome_text += (
        "Отправь мне голосовое сообщение с описанием события, и я добавлю его в твой Яндекс Календарь.\n\n"
        "Примеры:\n"
        "• \"Поставь встречу с клиентом на завтра в 15:00\"\n"
        "• \"Созвон с командой послезавтра в 10 утра на час\"\n"
        "• \"Напомни про презентацию через 2 дня в 14:30\"\n\n"
        "Используй /help для получения справки."
    )
    
    await message.answer(welcome_text)


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
        "/setup - Настроить учетные данные Яндекс Календаря\n"
        "/list - Показать ближайшие события"
    )


@dp.message(Command("setup"))
async def cmd_setup(message: Message):
    """Обработчик команды /setup для настройки учетных данных"""
    await message.answer(
        "🔐 Настройка учетных данных Яндекс Календаря\n\n"
        "Отправь мне пароль приложения от Яндекс Календаря в следующем формате:\n\n"
        "📧 Email: твой_email@yandex.ru\n"
        "🔑 Пароль: твой_пароль_приложения\n\n"
        "Или просто отправь пароль приложения, если email уже был указан.\n\n"
        "💡 Как получить пароль приложения:\n"
        "1. Зайди в настройки Яндекс ID\n"
        "2. Перейди в раздел 'Пароли приложений'\n"
        "3. Создай новый пароль для CalDAV\n"
        "4. Скопируй и отправь его мне"
    )


# Хранилище для временного хранения email при настройке
user_setup_state = {}


@dp.message(Command("list"))
async def cmd_list(message: Message):
    """Показать ближайшие события"""
    try:
        # Проверяем наличие учетных данных
        try:
            cal_service = await get_user_calendar_service(message.from_user.id)
        except ValueError as e:
            logger.error(f"Ошибка получения сервиса календаря: {e}")
            await message.answer(
                "❌ Учетные данные не настроены.\n\n"
                "Используй команду /setup для настройки учетных данных Яндекс Календаря."
            )
            return
        
        # Получаем события на ближайшие 7 дней
        timezone = pytz.timezone(Config.TIMEZONE)
        start_date = datetime.now(timezone)
        end_date = start_date + timedelta(days=7)
        
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
                logger.error(f"Ошибка обработки события: {e}", exc_info=True)
                continue
        
        await message.answer(text)
        
    except Exception as e:
        logger.error(f"Ошибка получения списка событий: {e}", exc_info=True)
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
        
        # Проверяем размер файла для информативного сообщения
        file_size = os.path.getsize(file_path)
        max_size = 1024 * 1024  # 1 МБ
        
        if file_size > max_size:
            size_mb = file_size / (1024 * 1024)
            await message.answer(
                f"🔤 Распознаю речь...\n"
                f"📊 Файл большой ({size_mb:.2f} МБ), разделяю на части для обработки."
            )
        else:
            await message.answer("🔤 Распознаю речь...")
        
        try:
            text = await transcription_service.transcribe_voice(file_path)
        except Exception as transcribe_error:
            error_msg = str(transcribe_error)
            logger.error(f"Ошибка транскрибации: {error_msg}")
            # Проверяем, связана ли ошибка с размером файла
            if "слишком большой" in error_msg.lower() or "большой файл" in error_msg.lower():
                await message.answer(
                    "❌ Аудиофайл слишком большой для обработки.\n\n"
                    "💡 Совет: Запишите более короткое голосовое сообщение (до 1 МБ)."
                )
            elif "распознавания речи" in error_msg.lower() or "speechkit" in error_msg.lower():
                await message.answer(
                    "❌ Не удалось распознать речь.\n\n"
                    "Попробуйте записать сообщение еще раз, убедившись, что:\n"
                    "• Микрофон работает корректно\n"
                    "• Речь четкая и разборчивая\n"
                    "• Сообщение не слишком длинное"
                )
            else:
                await message.answer(
                    "❌ Произошла ошибка при обработке голосового сообщения.\n\n"
                    "Попробуйте записать сообщение еще раз."
                )
            return
        
        if not text or len(text.strip()) == 0:
            await message.answer("❌ Не удалось распознать речь. Попробуйте записать сообщение еще раз.")
            return
        
        await message.answer(f"📝 Распознанный текст: \"{text}\"")
        
        # Обрабатываем текст через NLU
        await message.answer("🤖 Анализирую запрос...")
        events_info = await nlu_service.extract_event_info(text)
        
        # Проверяем наличие учетных данных перед созданием событий
        try:
            cal_service = await get_user_calendar_service(message.from_user.id)
        except ValueError as e:
            logger.error(f"Ошибка получения сервиса календаря: {e}")
            await message.answer(
                "❌ Учетные данные не настроены.\n\n"
                "Используй команду /setup для настройки учетных данных Яндекс Календаря."
            )
            return
        
        # Обрабатываем все события
        created_events = []
        errors = []
        
        for idx, event_info in enumerate(events_info):
            if event_info["action"] == "create_event":
                try:
                    await message.answer(f"📅 Создаю событие {idx + 1} из {len(events_info)}...")
                    
                    event_data = cal_service.create_event(
                        summary=event_info["summary"],
                        start_datetime=event_info["start_datetime"],
                        duration_minutes=event_info.get("duration_minutes", 60),
                        description=event_info.get("description")
                    )
                    
                    # Сохраняем событие в базу данных
                    db_event_id = await create_calendar_event(
                        event_id=event_data["event_id"],
                        summary=event_data["summary"],
                        start_datetime=event_data["start"],
                        end_datetime=event_data["end"],
                        telegram_user_id=message.from_user.id,
                        description=event_info.get("description")
                    )
                    
                    # Создаем уведомления
                    from scheduler import create_notifications
                    await create_notifications(db_event_id, event_data["start"])
                    
                    created_events.append({
                        "summary": event_data["summary"],
                        "start": event_data["start"],
                        "duration": event_info.get("duration_minutes", 60)
                    })
                except Exception as e:
                    logger.error(f"Ошибка создания события {idx + 1}: {e}", exc_info=True)
                    errors.append(f"Событие '{event_info.get('summary', 'Без названия')}': не удалось создать")
            else:
                errors.append(f"Действие '{event_info['action']}' пока не поддерживается.")
        
        # Формируем ответ пользователю
        if created_events:
            if len(created_events) == 1:
                event = created_events[0]
                start_str = event["start"].strftime("%d.%m.%Y в %H:%M")
                await message.answer(
                    f"✅ Событие успешно создано!\n\n"
                    f"📌 {event['summary']}\n"
                    f"📅 {start_str}\n"
                    f"⏱ Длительность: {event['duration']} минут\n\n"
                    f"Я напомню тебе за 60 и 15 минут до начала."
                )
            else:
                response_text = f"✅ Создано событий: {len(created_events)}\n\n"
                for i, event in enumerate(created_events, 1):
                    start_str = event["start"].strftime("%d.%m.%Y в %H:%M")
                    response_text += f"{i}. 📌 {event['summary']}\n   📅 {start_str}\n   ⏱ {event['duration']} минут\n\n"
                response_text += "Я напомню тебе за 60 и 15 минут до начала каждого события."
                await message.answer(response_text)
        
        if errors:
            error_text = "❌ Ошибки при создании событий:\n\n" + "\n".join(f"• {err}" for err in errors)
            await message.answer(error_text)
        
        # Удаляем временный файл
        try:
            await aiofiles_os.remove(file_path)
        except:
            pass
            
    except Exception as e:
        logger.error(f"Ошибка обработки голосового сообщения: {e}", exc_info=True)
        
        # Удаляем временный файл в случае ошибки
        try:
            file_path = os.path.join(TEMP_DIR, f"{message.voice.file_id}.ogg")
            await aiofiles_os.remove(file_path)
        except:
            pass
        
        await message.answer(
            "❌ Произошла ошибка при обработке голосового сообщения.\n\n"
            "Попробуйте записать сообщение еще раз или используйте /help для справки."
        )


def extract_credentials_from_text(text: str) -> tuple:
    """
    Извлечение email и пароля из текста
    
    Returns:
        tuple: (email, password) или (None, None) если не найдено
    """
    text = text.strip()
    
    # Паттерн для email
    email_pattern = r'([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})'
    # Паттерн для пароля (после ключевых слов или просто длинная строка)
    password_patterns = [
        r'(?:пароль|password|pass|пароль приложения)[:\s]+([^\s\n]+)',
        r'(?:🔑|ключ)[:\s]+([^\s\n]+)',
        r'пароль[:\s]*([a-zA-Z0-9\-_]{10,})',  # Пароль приложения обычно длинный
    ]
    
    email = None
    password = None
    
    # Ищем email
    email_match = re.search(email_pattern, text, re.IGNORECASE)
    if email_match:
        email = email_match.group(1).lower()
    
    # Ищем пароль
    for pattern in password_patterns:
        password_match = re.search(pattern, text, re.IGNORECASE)
        if password_match:
            password = password_match.group(1).strip()
            # Убираем возможные символы форматирования
            password = password.strip('*').strip('`').strip('"').strip("'")
            if len(password) >= 8:  # Минимальная длина пароля приложения
                break
            else:
                password = None
    
    # Если пароль не найден по паттернам, но текст выглядит как пароль
    if not password:
        # Если есть email в тексте, ищем пароль рядом
        if email_match:
            # Разбиваем текст на части относительно email
            email_text = email_match.group(0)
            parts = text.split(email_text)
            
            for part in parts:
                part = part.strip().strip(':').strip('-').strip()
                # Пароль приложения обычно содержит буквы, цифры, дефисы и подчеркивания
                if re.match(r'^[a-zA-Z0-9\-_]{10,}$', part):
                    password = part
                    break
        else:
            # Если нет email, проверяем, не является ли весь текст паролем
            # Пароль приложения обычно длинный (10+ символов) и содержит буквы и цифры
            if re.match(r'^[a-zA-Z0-9\-_]{10,}$', text) and len(text) >= 10:
                password = text
    
    return email, password


@dp.message()
async def handle_text(message: Message):
    """Обработчик текстовых сообщений"""
    text = message.text.strip()
    user_id = message.from_user.id
    
    # Пытаемся извлечь учетные данные из текста
    email, password = extract_credentials_from_text(text)
    
    # Если найден пароль или email, обрабатываем как настройку учетных данных
    if password or email:
        try:
            # Если есть email в тексте, используем его
            if email:
                # Если пароль не найден, возможно он был сохранен ранее или будет следующим сообщением
                if not password:
                    # Сохраняем email для следующего сообщения
                    user_setup_state[user_id] = {'email': email}
                    await message.answer(
                        f"✅ Email сохранен: {email}\n\n"
                        "Теперь отправь мне пароль приложения от Яндекс Календаря."
                    )
                    return
                else:
                    # Есть и email, и пароль
                    await save_user_credentials(user_id, email, password)
                    # Очищаем состояние настройки
                    user_setup_state.pop(user_id, None)
                    await message.answer(
                        f"✅ Учетные данные успешно сохранены!\n\n"
                        f"📧 Email: {email}\n"
                        f"🔑 Пароль: {'*' * len(password)}\n\n"
                        "Теперь ты можешь использовать бота для создания событий в календаре!"
                    )
                    return
            else:
                # Есть только пароль, проверяем сохраненный email
                if user_id in user_setup_state and 'email' in user_setup_state[user_id]:
                    email = user_setup_state[user_id]['email']
                    await save_user_credentials(user_id, email, password)
                    user_setup_state.pop(user_id, None)
                    await message.answer(
                        f"✅ Учетные данные успешно сохранены!\n\n"
                        f"📧 Email: {email}\n"
                        f"🔑 Пароль: {'*' * len(password)}\n\n"
                        "Теперь ты можешь использовать бота для создания событий в календаре!"
                    )
                    return
                else:
                    # Проверяем, есть ли уже сохраненные учетные данные
                    credentials = await get_user_credentials(user_id)
                        
                    if credentials:
                        # Обновляем только пароль
                        await save_user_credentials(user_id, credentials['yandex_user'], password)
                        await message.answer(
                            f"✅ Пароль успешно обновлен!\n\n"
                            f"📧 Email: {credentials['yandex_user']}\n"
                            f"🔑 Пароль: {'*' * len(password)}\n\n"
                            "Теперь ты можешь использовать бота для создания событий в календаре!"
                        )
                        return
                    else:
                        await message.answer(
                            "❌ Не найден email. Отправь мне учетные данные в формате:\n\n"
                            "📧 Email: твой_email@yandex.ru\n"
                            "🔑 Пароль: твой_пароль_приложения\n\n"
                            "Или используй команду /setup для инструкций."
                        )
                        return
        
        except Exception as e:
            logger.error(f"Ошибка сохранения учетных данных: {e}", exc_info=True)
            await message.answer(
                "❌ Произошла ошибка при сохранении учетных данных.\n\n"
                "Попробуй еще раз или используй команду /setup для инструкций."
            )
            return
    
    # Если это не учетные данные, показываем стандартное сообщение
    await message.answer(
        "📝 Я работаю с голосовыми сообщениями для создания событий в календаре.\n\n"
        "Отправь мне голосовое сообщение с описанием события, и я добавлю его в календарь.\n\n"
        "Для настройки учетных данных Яндекс Календаря используй команду /setup.\n\n"
        "Используй /help для получения справки."
    )


async def main():
    """Главная функция запуска бота"""
    # Проверяем конфигурацию (учетные данные Яндекс теперь не обязательны)
    try:
        # Проверяем только обязательные для работы бота переменные
        required = ["TELEGRAM_BOT_TOKEN", "YANDEX_SPEECHKIT_API_KEY", "YANDEX_SPEECHKIT_FOLDER_ID", "GEMINI_API_KEY"]
        missing = [var for var in required if not getattr(Config, var)]
        if missing:
            raise ValueError(f"Отсутствуют обязательные переменные окружения: {', '.join(missing)}")
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
    
    # Проверяем подключение к календарю (если есть глобальные учетные данные)
    if Config.YANDEX_USER and Config.YANDEX_PASS:
        try:
            test_service = YandexCalendarService()
            logger.info("Подключение к Яндекс Календарю успешно (глобальные учетные данные)")
        except Exception as e:
            logger.warning(f"Не удалось подключиться к Яндекс Календарю с глобальными учетными данными: {e}")
            logger.info("Пользователи смогут настроить свои учетные данные через команду /setup")
    else:
        logger.info("Глобальные учетные данные Яндекс не настроены. Пользователи смогут настроить их через команду /setup")
    
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
