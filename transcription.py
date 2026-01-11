"""Сервис транскрибации голосовых сообщений через OpenAI Whisper API"""
import aiofiles
import aiohttp
import os
import asyncio
import json
from config import Config
import logging

logger = logging.getLogger(__name__)

class TranscriptionService:
    """Сервис для преобразования голоса в текст через OpenAI Whisper API"""
    
    def __init__(self):
        self.api_key = Config.OPENAI_API_KEY
        self.api_url = "https://api.openai.com/v1/audio/transcriptions"
        self.max_size = 25 * 1024 * 1024  # 25 МБ в байтах (лимит OpenAI)
    
        
    async def _transcribe_audio(self, audio_data: bytes, session: aiohttp.ClientSession, max_retries: int = 3) -> str:
        """
        Транскрибирует аудио через OpenAI Whisper API с повторными попытками при ошибках
        
        Args:
            audio_data: Данные аудиофайла
            session: Сессия aiohttp
            max_retries: Максимальное количество повторных попыток
            
        Returns:
            Транскрибированный текст
        """
        headers = {
            "Authorization": f"Bearer {self.api_key}"
        }
        
        # OpenAI Whisper API поддерживает форматы: mp3, mp4, mpeg, mpga, m4a, wav, webm
        # Для Telegram голосовых сообщений (OGG) нужно конвертировать, но попробуем сначала отправить как есть
        
        data = aiohttp.FormData()
        data.add_field('file', audio_data, 
                      filename='audio.ogg', 
                      content_type='audio/ogg')
        data.add_field('model', 'whisper-1')
        data.add_field('language', 'ru')  # Указываем русский язык для лучшей точности
        
        last_error = None
        
        for attempt in range(max_retries):
            try:
                async with session.post(
                    self.api_url,
                    headers=headers,
                    data=data
                ) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        
                        # При ошибках сервера делаем повторную попытку
                        if response.status >= 500 and attempt < max_retries - 1:
                            wait_time = (attempt + 1) * 2  # Экспоненциальная задержка
                            logger.warning(
                                f"Ошибка сервера OpenAI (попытка {attempt + 1}/{max_retries}): "
                                f"{response.status} - {error_text}. Повтор через {wait_time} сек..."
                            )
                            await asyncio.sleep(wait_time)
                            last_error = Exception(f"Ошибка OpenAI API: {response.status}")
                            continue
                        
                        logger.error(f"Ошибка API OpenAI: {response.status} - {error_text}")
                        raise Exception(f"Ошибка распознавания речи: {error_text}")
                    
                    result = await response.json()
                    
                    if "text" not in result:
                        error_msg = result.get("error", "Неизвестная ошибка")
                        logger.error(f"Ошибка в ответе OpenAI: {error_msg}")
                        raise Exception(f"Ошибка распознавания речи: {error_msg}")
                    
                    text = result["text"]
                    return text.strip() if text else ""
                    
            except Exception as e:
                # Если это не ошибка сервера или последняя попытка, пробрасываем исключение
                if attempt == max_retries - 1 or not isinstance(e, aiohttp.ClientError):
                    raise
                last_error = e
                wait_time = (attempt + 1) * 2
                logger.warning(f"Исключение при транскрибации (попытка {attempt + 1}/{max_retries}): {e}. Повтор через {wait_time} сек...")
                await asyncio.sleep(wait_time)
        
        # Если все попытки исчерпаны, пробрасываем последнюю ошибку
        if last_error:
            raise last_error
        raise Exception("Не удалось транскрибировать аудио после всех попыток")
    
    async def transcribe_voice(self, audio_path: str) -> str:
        """
        Транскрибация голосового сообщения через OpenAI Whisper API.
        OpenAI поддерживает файлы до 25 МБ, поэтому разделение не требуется.
        
        Args:
            audio_path: Путь к аудиофайлу
            
        Returns:
            Транскрибированный текст
            
        Raises:
            Exception: Если произошла ошибка при транскрибации
        """
        try:
            file_size = os.path.getsize(audio_path)
            
            # Проверяем размер файла (лимит OpenAI - 25 МБ)
            if file_size > self.max_size:
                size_mb = file_size / (1024 * 1024)
                raise Exception(f"Файл слишком большой ({size_mb:.2f} МБ). Максимум: 25 МБ")
            
            logger.info(f"Начинаем транскрибацию файла: {audio_path} ({file_size / 1024:.1f} КБ)")
            
            # Читаем аудиофайл
            async with aiofiles.open(audio_path, "rb") as audio_file:
                audio_data = await audio_file.read()
            
            # Отправляем в OpenAI Whisper API
            async with aiohttp.ClientSession() as session:
                text = await self._transcribe_audio(audio_data, session)
                if not text:
                    raise Exception("Не удалось распознать речь. Попробуйте записать сообщение еще раз.")
                
                logger.info(f"✅ Транскрибация завершена: {text[:50]}...")
                return text
                
        except aiohttp.ClientError as e:
            logger.error(f"Ошибка сетевого запроса к OpenAI API: {e}")
            raise Exception(f"Ошибка подключения к сервису распознавания речи: {str(e)}")
        except Exception as e:
            logger.error(f"Ошибка транскрибации: {e}")
            raise
