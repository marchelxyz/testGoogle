"""Сервис транскрибации голосовых сообщений"""
import aiofiles
import os
from openai import OpenAI
from config import Config
import logging

logger = logging.getLogger(__name__)

class TranscriptionService:
    """Сервис для преобразования голоса в текст"""
    
    def __init__(self):
        self.client = OpenAI(api_key=Config.OPENAI_API_KEY)
    
    async def transcribe_voice(self, audio_path: str) -> str:
        """
        Транскрибация голосового сообщения
        
        Args:
            audio_path: Путь к аудиофайлу
            
        Returns:
            Транскрибированный текст
        """
        try:
            with open(audio_path, "rb") as audio_file:
                transcript = self.client.audio.transcriptions.create(
                    model="whisper-1",
                    file=audio_file,
                    language="ru"  # Указываем русский язык для лучшего качества
                )
            
            text = transcript.text
            logger.info(f"Транскрибация завершена: {text[:50]}...")
            return text
            
        except Exception as e:
            logger.error(f"Ошибка транскрибации: {e}")
            raise
