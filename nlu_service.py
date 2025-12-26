"""Сервис обработки естественного языка для извлечения данных о событиях"""
import google.generativeai as genai
from datetime import datetime, timedelta
from typing import Dict, Any, Optional
from config import Config
import json
import logging
import pytz
import asyncio
from dateutil import parser

logger = logging.getLogger(__name__)

class NLUService:
    """Сервис для обработки текста и извлечения информации о событиях"""
    
    def __init__(self):
        genai.configure(api_key=Config.GEMINI_API_KEY)
        self.model = genai.GenerativeModel(
            'gemini-1.5-flash',
            generation_config={
                "response_mime_type": "application/json",  # Включаем JSON режим
                "temperature": 0.3
            }
        )
        self.timezone = pytz.timezone(Config.TIMEZONE)
    
    def _get_current_datetime(self) -> datetime:
        """Получение текущей даты и времени в нужном часовом поясе"""
        return datetime.now(self.timezone)
    
    def _create_prompt(self, text: str) -> str:
        """Создание промпта для Gemini"""
        current_datetime = self._get_current_datetime()
        current_date_str = current_datetime.strftime("%Y-%m-%d %H:%M:%S")
        weekday_name = current_datetime.strftime("%A")  # День недели для контекста
        
        prompt = f"""Ты — помощник для управления календарем. Твоя задача — извлечь из текста пользователя детали события и вернуть их в формате JSON.

Текущая дата и время: {current_date_str} ({weekday_name}, часовой пояс: {Config.TIMEZONE})

Текст пользователя: "{text}"

Верни строго JSON со следующей структурой:
{{
    "action": "create_event" | "delete_event" | "update_event",
    "summary": "Название события",
    "start_datetime": "YYYY-MM-DD HH:MM:SS",
    "duration_minutes": 60,
    "description": "Описание (опционально, может быть null)"
}}

Правила:
1. Если пользователь говорит "завтра", "послезавтра", "через 3 дня" — вычисли правильную дату относительно текущей даты ({current_date_str})
2. Если указано время без даты (например, "в 3 часа дня"), используй сегодняшнюю дату, если событие еще не прошло, иначе завтрашнюю
3. Если время не указано, используй 12:00 по умолчанию
4. Если длительность не указана, используй 60 минут по умолчанию
5. Если пользователь просит удалить или изменить событие, укажи action соответственно
6. Если пользователь передумал внутри фразы (например, "на завтра, ой нет, на послезавтра"), бери последнее утверждение
7. Всегда возвращай валидный JSON, без дополнительного текста или комментариев

Примеры:
- "Поставь встречу с клиентом на завтра в 15:00" -> {{"action": "create_event", "summary": "Встреча с клиентом", "start_datetime": "2025-01-15 15:00:00", "duration_minutes": 60, "description": null}}
- "Созвон с командой послезавтра в 10 утра на час" -> {{"action": "create_event", "summary": "Созвон с командой", "start_datetime": "2025-01-16 10:00:00", "duration_minutes": 60, "description": null}}
- "Напомни мне про презентацию через 2 дня в 14:30" -> {{"action": "create_event", "summary": "Презентация", "start_datetime": "2025-01-16 14:30:00", "duration_minutes": 60, "description": null}}
- "Тренировка в пятницу в 6 вечера на полтора часа" -> {{"action": "create_event", "summary": "Тренировка", "start_datetime": "2025-01-17 18:00:00", "duration_minutes": 90, "description": null}}

Верни только JSON:"""
        
        return prompt
    
    async def extract_event_info(self, text: str) -> Dict[str, Any]:
        """
        Извлечение информации о событии из текста через Gemini 1.5 Flash
        
        Args:
            text: Транскрибированный текст
            
        Returns:
            Словарь с информацией о событии
        """
        try:
            prompt = self._create_prompt(text)
            
            # Отправляем запрос к Gemini (синхронный API, оборачиваем в executor)
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: self.model.generate_content(prompt)
            )
            
            # Получаем текст ответа
            result_text = response.text.strip()
            
            # Убираем возможные markdown блоки кода, если они есть
            if result_text.startswith("```json"):
                result_text = result_text[7:]
            if result_text.startswith("```"):
                result_text = result_text[3:]
            if result_text.endswith("```"):
                result_text = result_text[:-3]
            result_text = result_text.strip()
            
            # Парсим JSON
            result = json.loads(result_text)
            
            # Парсим дату и время
            if "start_datetime" in result:
                dt_str = result["start_datetime"]
                # Если дата без часового пояса, добавляем его
                try:
                    dt = parser.parse(dt_str)
                    if dt.tzinfo is None:
                        dt = self.timezone.localize(dt)
                    result["start_datetime"] = dt
                except Exception as e:
                    logger.error(f"Ошибка парсинга даты {dt_str}: {e}")
                    # Используем текущее время + 1 день как fallback
                    result["start_datetime"] = self._get_current_datetime() + timedelta(days=1)
            
            # Устанавливаем значения по умолчанию
            result.setdefault("action", "create_event")
            result.setdefault("duration_minutes", 60)
            result.setdefault("description", None)
            
            logger.info(f"Извлечена информация о событии: {result}")
            return result
            
        except json.JSONDecodeError as e:
            logger.error(f"Ошибка парсинга JSON от Gemini: {e}")
            logger.error(f"Ответ Gemini: {result_text if 'result_text' in locals() else 'N/A'}")
            raise ValueError("Не удалось обработать запрос. Попробуйте сформулировать иначе.")
        except Exception as e:
            logger.error(f"Ошибка обработки текста: {e}")
            raise
