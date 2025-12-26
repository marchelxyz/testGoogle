"""Сервис транскрибации голосовых сообщений"""
import aiofiles
import aiohttp
import os
from config import Config
import logging

logger = logging.getLogger(__name__)

class TranscriptionService:
    """Сервис для преобразования голоса в текст через Yandex SpeechKit"""
    
    def __init__(self):
        self.api_key = Config.YANDEX_SPEECHKIT_API_KEY
        self.folder_id = Config.YANDEX_SPEECHKIT_FOLDER_ID
        self.api_url = "https://stt.api.cloud.yandex.net/speech/v1/stt:recognize"
    
    async def transcribe_voice(self, audio_path: str) -> str:
        """
        Транскрибация голосового сообщения через Yandex SpeechKit
        
        Args:
            audio_path: Путь к аудиофайлу
            
        Returns:
            Транскрибированный текст
            
        Raises:
            Exception: Если файл слишком большой или произошла другая ошибка
        """
        try:
            # Проверяем размер файла (Yandex SpeechKit требует файлы меньше 1 МБ)
            file_size = os.path.getsize(audio_path)
            max_size = 1024 * 1024  # 1 МБ в байтах
            
            if file_size > max_size:
                size_mb = file_size / (1024 * 1024)
                logger.error(f"Аудиофайл слишком большой: {size_mb:.2f} МБ (максимум 1 МБ)")
                raise Exception(
                    f"Аудиофайл слишком большой ({size_mb:.2f} МБ). "
                    "Yandex SpeechKit поддерживает файлы до 1 МБ. "
                    "Пожалуйста, запишите более короткое голосовое сообщение."
                )
            
            # Читаем аудиофайл
            async with aiofiles.open(audio_path, "rb") as audio_file:
                audio_data = await audio_file.read()
            
            # Параметры запроса
            params = {
                "lang": "ru-RU",  # Русский язык
                "folderId": self.folder_id,
                "format": "oggopus",  # Формат голосовых сообщений Telegram
                "sampleRateHertz": "48000"  # Частота дискретизации для OGG Opus
            }
            
            # Заголовки запроса
            headers = {
                "Authorization": f"Api-Key {self.api_key}"
            }
            
            # Отправляем запрос к Yandex SpeechKit
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.api_url,
                    params=params,
                    headers=headers,
                    data=audio_data
                ) as response:
                    if response.status != 200:
                        # Читаем тело ответа один раз
                        error_data = await response.read()
                        error_text = error_data.decode('utf-8', errors='ignore')
                        logger.error(f"Ошибка API Yandex SpeechKit: {response.status} - {error_text}")
                        
                        # Пытаемся распарсить как JSON
                        error_msg = None
                        try:
                            import json
                            error_json = json.loads(error_text)
                            error_msg = error_json.get("error", {}).get("error_message", error_text)
                        except:
                            error_msg = error_text
                        
                        # Проверяем на ошибку размера файла
                        if error_msg and "audio should be less than 1 mb" in error_msg.lower():
                            raise Exception(
                                "Аудиофайл слишком большой (более 1 МБ). "
                                "Пожалуйста, запишите более короткое голосовое сообщение."
                            )
                        
                        raise Exception(f"Ошибка распознавания речи: {error_msg or response.status}")
                    
                    result = await response.json()
                    
                    if "result" not in result:
                        error_msg = result.get("error", {}).get("message", "Неизвестная ошибка")
                        logger.error(f"Ошибка в ответе Yandex SpeechKit: {error_msg}")
                        raise Exception(f"Ошибка распознавания речи: {error_msg}")
                    
                    text = result["result"]
                    
                    if not text or len(text.strip()) == 0:
                        logger.warning("Yandex SpeechKit вернул пустой текст")
                        raise Exception("Не удалось распознать речь. Попробуйте записать сообщение еще раз.")
                    
                    logger.info(f"Транскрибация завершена: {text[:50]}...")
                    return text.strip()
            
        except aiohttp.ClientError as e:
            logger.error(f"Ошибка сетевого запроса к Yandex SpeechKit: {e}")
            raise Exception(f"Ошибка подключения к сервису распознавания речи: {str(e)}")
        except Exception as e:
            logger.error(f"Ошибка транскрибации: {e}")
            raise
