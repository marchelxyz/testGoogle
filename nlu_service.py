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
    
    # Порядок приоритета моделей Gemini с автоматическим fallback
    MODEL_PRIORITIES = [
        'gemini-2.5-flash',  # Приоритет 1 - самая новая и быстрая
        'gemini-1.5-flash',  # Приоритет 2 - быстрая и широко доступная
        'gemini-1.5-pro',    # Приоритет 3 - более мощная модель
        'gemini-pro'         # Приоритет 4 - legacy версия для совместимости
    ]
    
    def __init__(self):
        genai.configure(api_key=Config.GEMINI_API_KEY)
        self.model = None
        self.model_name = None
        self.timezone = pytz.timezone(Config.TIMEZONE)
        self._initialize_model()
    
    def _initialize_model(self):
        """Инициализация модели Gemini с автоматическим fallback"""
        for model_name in self.MODEL_PRIORITIES:
            try:
                logger.info(f"Попытка инициализации модели: {model_name}")
                model = genai.GenerativeModel(
                    model_name,
                    generation_config={
                        "response_mime_type": "application/json",
                        "temperature": 0.3
                    }
                )
                self.model = model
                self.model_name = model_name
                logger.info(f"Успешно инициализирована модель: {model_name}")
                return
            except Exception as e:
                logger.warning(f"Модель {model_name} недоступна при инициализации: {e}")
                continue
        
        # Если ни одна модель не доступна при инициализации, повторная попытка будет при первом запросе
        logger.warning("Ни одна модель не доступна при инициализации. Будет повторная попытка при первом запросе.")
        self.model = None
        self.model_name = None
    
    def _ensure_model_initialized(self):
        """Обеспечивает инициализацию модели, если она еще не инициализирована"""
        if self.model is not None:
            return
        
        logger.info("Повторная попытка инициализации модели при первом запросе")
        for model_name in self.MODEL_PRIORITIES:
            try:
                logger.info(f"Попытка инициализации модели: {model_name}")
                model = genai.GenerativeModel(
                    model_name,
                    generation_config={
                        "response_mime_type": "application/json",
                        "temperature": 0.3
                    }
                )
                self.model = model
                self.model_name = model_name
                logger.info(f"Успешно инициализирована модель: {model_name}")
                return
            except Exception as e:
                logger.warning(f"Модель {model_name} недоступна: {e}")
                continue
        
        raise RuntimeError("Не удалось инициализировать ни одну из доступных моделей Gemini")
    
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
    
    def _try_models_with_fallback(self, prompt: str) -> str:
        """
        Выполняет запрос к модели с автоматическим fallback на следующую модель при ошибке
        
        Args:
            prompt: Промпт для отправки в модель
            
        Returns:
            Текст ответа от модели
        """
        # Список моделей для попытки (начинаем с текущей, затем пробуем остальные)
        models_to_try = []
        if self.model_name:
            # Начинаем с текущей модели
            current_index = self.MODEL_PRIORITIES.index(self.model_name) if self.model_name in self.MODEL_PRIORITIES else 0
            models_to_try = self.MODEL_PRIORITIES[current_index:] + self.MODEL_PRIORITIES[:current_index]
        else:
            # Если модель не инициализирована, пробуем все по порядку
            models_to_try = self.MODEL_PRIORITIES
        
        last_error = None
        for model_name in models_to_try:
            try:
                # Если это не текущая модель, создаем новую
                if model_name != self.model_name:
                    logger.info(f"Попытка использовать модель: {model_name}")
                    model = genai.GenerativeModel(
                        model_name,
                        generation_config={
                            "response_mime_type": "application/json",
                            "temperature": 0.3
                        }
                    )
                else:
                    model = self.model
                
                # Выполняем запрос
                response = model.generate_content(prompt)
                result_text = response.text.strip()
                
                # Если успешно и использовали другую модель, обновляем текущую
                if model_name != self.model_name:
                    self.model = model
                    self.model_name = model_name
                    logger.info(f"Успешно переключились на модель: {model_name}")
                
                return result_text
                
            except Exception as e:
                logger.warning(f"Ошибка при использовании модели {model_name}: {e}")
                last_error = e
                continue
        
        # Если все модели не сработали, выбрасываем последнюю ошибку
        raise RuntimeError(f"Не удалось выполнить запрос ни к одной из моделей. Последняя ошибка: {last_error}")
    
    async def extract_event_info(self, text: str) -> Dict[str, Any]:
        """
        Извлечение информации о событии из текста через Gemini с автоматическим fallback
        
        Args:
            text: Транскрибированный текст
            
        Returns:
            Словарь с информацией о событии
        """
        try:
            # Убеждаемся, что модель инициализирована
            self._ensure_model_initialized()
            
            prompt = self._create_prompt(text)
            
            # Отправляем запрос к Gemini с автоматическим fallback (синхронный API, оборачиваем в executor)
            loop = asyncio.get_event_loop()
            result_text = await loop.run_in_executor(
                None,
                lambda: self._try_models_with_fallback(prompt)
            )
            
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
            
            # Проверяем, что result является словарем, а не списком
            if isinstance(result, list):
                if len(result) > 0:
                    # Если это список, берем первый элемент
                    result = result[0]
                    logger.warning("Gemini вернул список вместо объекта, используется первый элемент")
                else:
                    # Если список пустой, создаем словарь по умолчанию
                    result = {}
                    logger.warning("Gemini вернул пустой список, используется словарь по умолчанию")
            
            # Убеждаемся, что result является словарем
            if not isinstance(result, dict):
                logger.error(f"Неожиданный тип результата: {type(result)}, значение: {result}")
                raise ValueError("Не удалось обработать ответ от модели. Попробуйте сформулировать иначе.")
            
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
