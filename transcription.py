"""Сервис транскрибации голосовых сообщений"""
import aiofiles
import aiohttp
import os
import asyncio
import json
import shutil
from config import Config
import logging

logger = logging.getLogger(__name__)

class TranscriptionService:
    """Сервис для преобразования голоса в текст через Yandex SpeechKit"""
    
    def __init__(self):
        self.api_key = Config.YANDEX_SPEECHKIT_API_KEY
        self.folder_id = Config.YANDEX_SPEECHKIT_FOLDER_ID
        self.api_url = "https://stt.api.cloud.yandex.net/speech/v1/stt:recognize"
        self.max_size = 1024 * 1024  # 1 МБ в байтах
    
    async def _get_audio_duration(self, audio_path: str) -> float:
        """
        Получает длительность аудиофайла в секундах через ffprobe.
        
        ffprobe - это утилита из пакета ffmpeg (консольная программа, не Python библиотека).
        Если ffprobe недоступен, используется оценка на основе размера файла.
        
        Args:
            audio_path: Путь к аудиофайлу
            
        Returns:
            Длительность в секундах
        """
        try:
            # ffprobe - консольная утилита из пакета ffmpeg
            # Вызывается через subprocess, так как это не Python библиотека
            cmd = [
                'ffprobe',
                '-v', 'error',
                '-show_entries', 'format=duration',
                '-of', 'json',
                audio_path
            ]
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate()
            
            if process.returncode != 0:
                error_msg = stderr.decode('utf-8', errors='ignore')
                logger.warning(f"Не удалось получить длительность через ffprobe: {error_msg}")
                # Возвращаем примерную оценку на основе размера файла
                file_size = os.path.getsize(audio_path)
                # Примерно 10-15 КБ/секунда для OGG Opus
                estimated_duration = file_size / (12 * 1024)
                return estimated_duration
            
            result = json.loads(stdout.decode('utf-8'))
            duration = float(result.get('format', {}).get('duration', 0))
            return duration
        except FileNotFoundError:
            logger.warning("ffprobe не найден, используем оценку по размеру файла")
            file_size = os.path.getsize(audio_path)
            estimated_duration = file_size / (12 * 1024)  # Примерно 12 КБ/секунда
            return estimated_duration
        except Exception as e:
            logger.warning(f"Ошибка при получении длительности: {e}")
            file_size = os.path.getsize(audio_path)
            estimated_duration = file_size / (12 * 1024)
            return estimated_duration
    
    async def _split_audio_file(self, audio_path: str, start_time: float, duration: float, output_path: str) -> bool:
        """
        Разделяет аудиофайл на часть используя ffmpeg.
        
        ffmpeg - это консольная утилита (командная программа), не Python библиотека.
        Устанавливается отдельно в операционную систему.
        Вызывается через subprocess для работы с аудио файлами.
        
        Альтернативы:
        - pydub (Python библиотека) - но она тоже требует ffmpeg под капотом
        - Другие библиотеки - но для OGG Opus (формат Telegram) ffmpeg - стандарт
        
        Args:
            audio_path: Путь к исходному файлу
            start_time: Начало сегмента в секундах
            duration: Длительность сегмента в секундах
            output_path: Путь для сохранения части
            
        Returns:
            True если успешно, False иначе
        """
        try:
            # ffmpeg - консольная утилита, вызывается через subprocess
            # Устанавливается отдельно: apt-get install ffmpeg / brew install ffmpeg
            cmd = [
                'ffmpeg',
                '-i', audio_path,
                '-ss', str(start_time),
                '-t', str(duration),
                '-acodec', 'copy',  # Копируем кодек без перекодирования
                '-y',  # Перезаписываем выходной файл
                output_path
            ]
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate()
            
            if process.returncode != 0:
                error_msg = stderr.decode('utf-8', errors='ignore')
                logger.error(f"Ошибка разделения аудио: {error_msg}")
                return False
            
            # Проверяем, что файл создан и имеет разумный размер
            if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                return True
            return False
        except FileNotFoundError:
            logger.error("ffmpeg не найден. Установите ffmpeg для разделения больших файлов.")
            return False
        except Exception as e:
            logger.error(f"Ошибка при разделении аудио: {e}")
            return False
    
    async def _transcribe_chunk(self, audio_data: bytes, session: aiohttp.ClientSession) -> str:
        """
        Транскрибирует один фрагмент аудио
        
        Args:
            audio_data: Данные аудиофайла
            session: Сессия aiohttp
            
        Returns:
            Транскрибированный текст
        """
        params = {
            "lang": "ru-RU",
            "folderId": self.folder_id,
            "format": "oggopus",
            "sampleRateHertz": "48000"
        }
        
        headers = {
            "Authorization": f"Api-Key {self.api_key}"
        }
        
        async with session.post(
            self.api_url,
            params=params,
            headers=headers,
            data=audio_data
        ) as response:
            if response.status != 200:
                error_data = await response.read()
                error_text = error_data.decode('utf-8', errors='ignore')
                logger.error(f"Ошибка API Yandex SpeechKit: {response.status} - {error_text}")
                
                error_msg = None
                try:
                    error_json = json.loads(error_text)
                    error_msg = error_json.get("error", {}).get("error_message", error_text)
                except:
                    error_msg = error_text
                
                raise Exception(f"Ошибка распознавания речи: {error_msg or response.status}")
            
            result = await response.json()
            
            if "result" not in result:
                error_msg = result.get("error", {}).get("message", "Неизвестная ошибка")
                logger.error(f"Ошибка в ответе Yandex SpeechKit: {error_msg}")
                raise Exception(f"Ошибка распознавания речи: {error_msg}")
            
            text = result["result"]
            return text.strip() if text else ""
    
    async def transcribe_voice(self, audio_path: str) -> str:
        """
        Транскрибация голосового сообщения через Yandex SpeechKit.
        Если файл больше 1 МБ, автоматически разделяет на части.
        
        Args:
            audio_path: Путь к аудиофайлу
            
        Returns:
            Транскрибированный текст
            
        Raises:
            Exception: Если произошла ошибка при транскрибации
        """
        try:
            file_size = os.path.getsize(audio_path)
            
            # Если файл меньше 1 МБ, обрабатываем как обычно
            if file_size <= self.max_size:
                async with aiofiles.open(audio_path, "rb") as audio_file:
                    audio_data = await audio_file.read()
                
                async with aiohttp.ClientSession() as session:
                    text = await self._transcribe_chunk(audio_data, session)
                    if not text:
                        raise Exception("Не удалось распознать речь. Попробуйте записать сообщение еще раз.")
                    logger.info(f"Транскрибация завершена: {text[:50]}...")
                    return text
            
            # Файл больше 1 МБ - разделяем на части
            logger.info(f"Файл слишком большой ({file_size / (1024*1024):.2f} МБ), разделяем на части...")
            
            # Проверяем наличие ffmpeg
            try:
                # Проверяем доступность ffmpeg
                process = await asyncio.create_subprocess_exec(
                    'ffmpeg', '-version',
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                await process.communicate()
                if process.returncode != 0:
                    raise FileNotFoundError("ffmpeg не работает корректно")
            except FileNotFoundError:
                size_mb = file_size / (1024 * 1024)
                logger.error("ffmpeg не найден. Невозможно разделить большой файл.")
                raise Exception(
                    f"Аудиофайл слишком большой ({size_mb:.2f} МБ). "
                    "Для обработки больших файлов требуется установить ffmpeg. "
                    "Или запишите более короткое голосовое сообщение (до 1 МБ)."
                )
            
            # Получаем длительность файла
            total_duration = await self._get_audio_duration(audio_path)
            if total_duration <= 0:
                raise Exception("Не удалось определить длительность аудиофайла")
            
            # Оцениваем размер одной секунды аудио
            bytes_per_second = file_size / total_duration
            
            # Вычисляем длительность части, которая будет меньше 1 МБ (с запасом 10%)
            chunk_duration = (self.max_size * 0.9) / bytes_per_second
            
            # Создаем временную директорию для частей
            temp_dir = os.path.join(os.path.dirname(audio_path), "chunks")
            os.makedirs(temp_dir, exist_ok=True)
            
            try:
                chunks_texts = []
                num_chunks = int((total_duration / chunk_duration) + 1)
                
                logger.info(f"Разделяем файл на {num_chunks} частей (по {chunk_duration:.1f} сек каждая)")
                
                async with aiohttp.ClientSession() as session:
                    for i in range(num_chunks):
                        start_time = i * chunk_duration
                        if start_time >= total_duration:
                            break
                        
                        # Длительность текущей части
                        current_duration = min(chunk_duration, total_duration - start_time)
                        
                        # Создаем временный файл для части
                        chunk_path = os.path.join(temp_dir, f"chunk_{i}.ogg")
                        
                        # Разделяем файл
                        success = await self._split_audio_file(audio_path, start_time, current_duration, chunk_path)
                        if not success:
                            logger.warning(f"Не удалось создать часть {i+1}, пропускаем")
                            continue
                        
                        # Проверяем размер части
                        chunk_size = os.path.getsize(chunk_path)
                        if chunk_size > self.max_size:
                            logger.warning(f"Часть {i+1} все еще слишком большая ({chunk_size / (1024*1024):.2f} МБ), пропускаем")
                            try:
                                os.remove(chunk_path)
                            except:
                                pass
                            continue
                        
                        # Транскрибируем часть
                        try:
                            async with aiofiles.open(chunk_path, "rb") as chunk_file:
                                chunk_data = await chunk_file.read()
                            
                            chunk_text = await self._transcribe_chunk(chunk_data, session)
                            if chunk_text:
                                chunks_texts.append(chunk_text)
                                logger.info(f"Часть {i+1}/{num_chunks} обработана: {len(chunk_text)} символов")
                        except Exception as e:
                            logger.error(f"Ошибка при транскрибации части {i+1}: {e}")
                            # Продолжаем обработку остальных частей
                        finally:
                            # Удаляем временный файл части
                            try:
                                os.remove(chunk_path)
                            except:
                                pass
                
                if not chunks_texts:
                    raise Exception("Не удалось распознать ни одну часть аудиофайла")
                
                # Объединяем результаты
                full_text = " ".join(chunks_texts)
                logger.info(f"Транскрибация завершена: {len(chunks_texts)} частей, всего {len(full_text)} символов")
                return full_text.strip()
                
            finally:
                # Удаляем временную директорию
                try:
                    if os.path.exists(temp_dir):
                        shutil.rmtree(temp_dir)
                except:
                    pass
            
        except aiohttp.ClientError as e:
            logger.error(f"Ошибка сетевого запроса к Yandex SpeechKit: {e}")
            raise Exception(f"Ошибка подключения к сервису распознавания речи: {str(e)}")
        except Exception as e:
            logger.error(f"Ошибка транскрибации: {e}")
            raise
