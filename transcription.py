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

# Попытка импортировать mutagen для работы с OGG файлами
try:
    from mutagen.oggopus import OggOpus
    MUTAGEN_AVAILABLE = True
except ImportError:
    MUTAGEN_AVAILABLE = False
    logger.warning("mutagen не установлен, будет использован упрощенный метод разделения файлов")

class TranscriptionService:
    """Сервис для преобразования голоса в текст через Yandex SpeechKit"""
    
    def __init__(self):
        self.api_key = Config.YANDEX_SPEECHKIT_API_KEY
        self.folder_id = Config.YANDEX_SPEECHKIT_FOLDER_ID
        self.api_url = "https://stt.api.cloud.yandex.net/speech/v1/stt:recognize"
        self.max_size = 1024 * 1024  # 1 МБ в байтах
        self._ffmpeg_available = None  # Кэш для проверки наличия ffmpeg
    
    async def _check_ffmpeg_available(self) -> bool:
        """
        Проверяет доступность ffmpeg в системе.
        Результат кэшируется для избежания повторных проверок.
        
        Returns:
            True если ffmpeg доступен, False иначе
        """
        if self._ffmpeg_available is not None:
            return self._ffmpeg_available
        
        try:
            process = await asyncio.create_subprocess_exec(
                'ffmpeg', '-version',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await process.communicate()
            self._ffmpeg_available = (process.returncode == 0)
            if not self._ffmpeg_available:
                logger.warning("ffmpeg найден, но не работает корректно")
            return self._ffmpeg_available
        except FileNotFoundError:
            logger.warning("ffmpeg не найден в системе")
            self._ffmpeg_available = False
            return False
        except Exception as e:
            logger.warning(f"Ошибка при проверке ffmpeg: {e}")
            self._ffmpeg_available = False
            return False
    
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
                # Пытаемся использовать mutagen для OGG файлов
                if MUTAGEN_AVAILABLE:
                    try:
                        audio_file = OggOpus(audio_path)
                        if audio_file.length is not None:
                            duration = float(audio_file.length)
                            if duration > 0:
                                return duration
                    except Exception as e:
                        logger.debug(f"Не удалось получить длительность через mutagen: {e}")
                
                # Возвращаем консервативную оценку на основе размера файла
                file_size = os.path.getsize(audio_path)
                # Используем 10 КБ/секунда для консервативной оценки OGG Opus
                estimated_duration = file_size / (10 * 1024)
                return estimated_duration
            
            result = json.loads(stdout.decode('utf-8'))
            duration = float(result.get('format', {}).get('duration', 0))
            return duration
        except FileNotFoundError:
            logger.warning("ffprobe не найден, используем оценку по размеру файла")
            # Пытаемся использовать mutagen для OGG файлов
            if MUTAGEN_AVAILABLE:
                try:
                    audio_file = OggOpus(audio_path)
                    if audio_file.length is not None:
                        duration = float(audio_file.length)
                        if duration > 0:
                            return duration
                except Exception as e:
                    logger.debug(f"Не удалось получить длительность через mutagen: {e}")
            
            file_size = os.path.getsize(audio_path)
            # Используем более консервативную оценку: 10 КБ/секунда для OGG Opus
            # Это гарантирует, что части не будут превышать лимиты
            estimated_duration = file_size / (10 * 1024)
            return estimated_duration
        except Exception as e:
            logger.warning(f"Ошибка при получении длительности: {e}")
            # Пытаемся использовать mutagen для OGG файлов
            if MUTAGEN_AVAILABLE:
                try:
                    audio_file = OggOpus(audio_path)
                    if audio_file.length is not None:
                        duration = float(audio_file.length)
                        if duration > 0:
                            return duration
                except Exception:
                    pass
            
            file_size = os.path.getsize(audio_path)
            # Используем более консервативную оценку: 10 КБ/секунда
            estimated_duration = file_size / (10 * 1024)
            return estimated_duration
    
    def _find_ogg_page_boundary(self, data: bytes, start_pos: int = 0) -> int:
        """
        Находит границу следующей OGG страницы в данных.
        OGG страницы начинаются с магического числа 'OggS' (0x4F676753).
        
        Args:
            data: Данные файла
            start_pos: Позиция начала поиска
            
        Returns:
            Позиция начала следующей OGG страницы или -1 если не найдено
        """
        magic = b'OggS'
        pos = data.find(magic, start_pos)
        return pos if pos >= 0 else -1
    
    async def _split_audio_file_by_bytes(self, audio_path: str, start_byte: int, chunk_size: int, output_path: str) -> bool:
        """
        Разделяет аудиофайл на части по байтам без использования ffmpeg.
        Пытается найти границы OGG страниц для более корректного разделения.
        
        Args:
            audio_path: Путь к исходному файлу
            start_byte: Начальная позиция в байтах
            chunk_size: Размер части в байтах
            output_path: Путь для сохранения части
            
        Returns:
            True если успешно, False иначе
        """
        try:
            file_size = os.path.getsize(audio_path)
            
            # Ограничиваем размер части
            chunk_size = min(chunk_size, int(self.max_size * 0.9))
            start_byte = max(0, min(start_byte, file_size - 1))
            end_byte = min(start_byte + chunk_size, file_size)
            
            async with aiofiles.open(audio_path, "rb") as audio_file:
                # Если это не первая часть, пытаемся найти начало OGG страницы
                if start_byte > 0:
                    # Читаем данные с небольшим запасом для поиска границы страницы
                    search_start = max(0, start_byte - 5000)  # Ищем границу в пределах 5 КБ назад
                    await audio_file.seek(search_start)
                    search_data = await audio_file.read(min(end_byte - search_start + 1000, file_size - search_start))
                    
                    # Ищем начало OGG страницы в области поиска
                    page_start_in_search = self._find_ogg_page_boundary(search_data)
                    if page_start_in_search >= 0:
                        # Найдена граница страницы, используем её
                        actual_start = search_start + page_start_in_search
                        await audio_file.seek(actual_start)
                        actual_chunk_size = min(end_byte - actual_start, int(self.max_size * 0.9))
                        chunk_data = await audio_file.read(actual_chunk_size)
                    else:
                        # Граница не найдена, используем исходную позицию
                        await audio_file.seek(start_byte)
                        chunk_data = await audio_file.read(end_byte - start_byte)
                else:
                    # Первая часть - читаем с начала
                    await audio_file.seek(0)
                    chunk_data = await audio_file.read(end_byte)
                
                if not chunk_data or len(chunk_data) == 0:
                    return False
                
                # Проверяем, что данные начинаются с OGG заголовка (для первой части)
                # или содержат валидные данные
                if start_byte == 0 and not chunk_data.startswith(b'OggS'):
                    logger.warning("Первая часть файла не начинается с OGG заголовка")
                    # Все равно пытаемся использовать данные
                
                # Сохраняем часть файла
                async with aiofiles.open(output_path, "wb") as output_file:
                    await output_file.write(chunk_data)
                
                # Проверяем, что файл создан и имеет разумный размер
                if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                    return True
                return False
        except Exception as e:
            logger.error(f"Ошибка при разделении аудио по байтам: {e}")
            return False
    
    async def _split_audio_file(self, audio_path: str, start_time: float, duration: float, output_path: str) -> bool:
        """
        Разделяет аудиофайл на часть. Сначала пытается использовать ffmpeg,
        если недоступен - использует альтернативный метод разделения по байтам.
        
        Args:
            audio_path: Путь к исходному файлу
            start_time: Начало сегмента в секундах
            duration: Длительность сегмента в секундах
            output_path: Путь для сохранения части
            
        Returns:
            True если успешно, False иначе
        """
        # Сначала пробуем использовать ffmpeg, если доступен
        ffmpeg_available = await self._check_ffmpeg_available()
        
        if ffmpeg_available:
            try:
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
                
                if process.returncode == 0:
                    # Проверяем, что файл создан и имеет разумный размер
                    if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                        return True
                
                logger.warning("ffmpeg не смог разделить файл, используем альтернативный метод")
            except Exception as e:
                logger.warning(f"Ошибка при использовании ffmpeg: {e}, используем альтернативный метод")
        
        # Альтернативный метод: разделение по байтам
        try:
            file_size = os.path.getsize(audio_path)
            
            # Оцениваем размер одной секунды аудио
            # Используем консервативную оценку: 10 КБ/секунда для OGG Opus
            audio_duration = await self._get_audio_duration(audio_path)
            if audio_duration <= 0:
                audio_duration = file_size / (10 * 1024)
            estimated_bytes_per_second = file_size / max(1, audio_duration)
            
            # Вычисляем позиции в байтах
            start_byte = int(start_time * estimated_bytes_per_second)
            chunk_size = int(duration * estimated_bytes_per_second)
            
            # Ограничиваем размер части
            chunk_size = min(chunk_size, int(self.max_size * 0.9))
            start_byte = min(start_byte, file_size - 1)
            
            return await self._split_audio_file_by_bytes(audio_path, start_byte, chunk_size, output_path)
        except Exception as e:
            logger.error(f"Ошибка при альтернативном разделении аудио: {e}")
            return False
    
    async def _transcribe_chunk(self, audio_data: bytes, session: aiohttp.ClientSession, max_retries: int = 3) -> str:
        """
        Транскрибирует один фрагмент аудио с повторными попытками при ошибках сервера
        
        Args:
            audio_data: Данные аудиофайла
            session: Сессия aiohttp
            max_retries: Максимальное количество повторных попыток
            
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
        
        last_error = None
        
        for attempt in range(max_retries):
            try:
                async with session.post(
                    self.api_url,
                    params=params,
                    headers=headers,
                    data=audio_data
                ) as response:
                    if response.status != 200:
                        error_data = await response.read()
                        error_text = error_data.decode('utf-8', errors='ignore')
                        
                        error_msg = None
                        error_code = None
                        try:
                            error_json = json.loads(error_text)
                            error_code = error_json.get("error_code")
                            error_msg = error_json.get("error_message", error_text)
                        except:
                            error_msg = error_text
                        
                        # Для INTERNAL_SERVER_ERROR делаем повторную попытку
                        if response.status == 500 or (error_code == "INTERNAL_SERVER_ERROR" and attempt < max_retries - 1):
                            wait_time = (attempt + 1) * 2  # Экспоненциальная задержка: 2, 4, 6 секунд
                            logger.warning(
                                f"Ошибка сервера (попытка {attempt + 1}/{max_retries}): "
                                f"{response.status} - {error_msg}. Повтор через {wait_time} сек..."
                            )
                            await asyncio.sleep(wait_time)
                            last_error = Exception(f"Ошибка распознавания речи: {error_msg or response.status}")
                            continue
                        
                        # Для других ошибок не делаем повторные попытки
                        logger.error(f"Ошибка API Yandex SpeechKit: {response.status} - {error_text}")
                        raise Exception(f"Ошибка распознавания речи: {error_msg or response.status}")
                    
                    result = await response.json()
                    
                    if "result" not in result:
                        error_msg = result.get("error", {}).get("message", "Неизвестная ошибка")
                        error_code = result.get("error", {}).get("error_code")
                        
                        # Для INTERNAL_SERVER_ERROR делаем повторную попытку
                        if error_code == "INTERNAL_SERVER_ERROR" and attempt < max_retries - 1:
                            wait_time = (attempt + 1) * 2
                            logger.warning(
                                f"Ошибка сервера в ответе (попытка {attempt + 1}/{max_retries}): "
                                f"{error_msg}. Повтор через {wait_time} сек..."
                            )
                            await asyncio.sleep(wait_time)
                            last_error = Exception(f"Ошибка распознавания речи: {error_msg}")
                            continue
                        
                        logger.error(f"Ошибка в ответе Yandex SpeechKit: {error_msg}")
                        raise Exception(f"Ошибка распознавания речи: {error_msg}")
                    
                    text = result["result"]
                    return text.strip() if text else ""
                    
            except Exception as e:
                # Если это не ошибка сервера или последняя попытка, пробрасываем исключение
                if attempt == max_retries - 1 or "INTERNAL_SERVER_ERROR" not in str(e):
                    raise
                last_error = e
                wait_time = (attempt + 1) * 2
                logger.warning(f"Исключение при транскрибации (попытка {attempt + 1}/{max_retries}): {e}. Повтор через {wait_time} сек...")
                await asyncio.sleep(wait_time)
        
        # Если все попытки исчерпаны, пробрасываем последнюю ошибку
        if last_error:
            raise last_error
        raise Exception("Не удалось транскрибировать фрагмент после всех попыток")
    
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
            
            # Получаем длительность файла для проверки ограничения в 30 секунд
            total_duration = await self._get_audio_duration(audio_path)
            if total_duration <= 0:
                # Если не удалось определить длительность, используем консервативную оценку по размеру
                # Используем 10 КБ/секунда для гарантии, что части не превысят лимиты
                total_duration = file_size / (10 * 1024)
            
            # Если файл меньше 1 МБ и короче 30 секунд, обрабатываем как обычно
            if file_size <= self.max_size and total_duration <= 30.0:
                async with aiofiles.open(audio_path, "rb") as audio_file:
                    audio_data = await audio_file.read()
                
                async with aiohttp.ClientSession() as session:
                    text = await self._transcribe_chunk(audio_data, session)
                    if not text:
                        raise Exception("Не удалось распознать речь. Попробуйте записать сообщение еще раз.")
                    logger.info(f"Транскрибация завершена: {text[:50]}...")
                    return text
            
            # Файл нужно разделить: либо слишком большой, либо слишком длинный
            size_mb = file_size / (1024 * 1024)
            if file_size > self.max_size:
                logger.info(f"Файл слишком большой ({size_mb:.2f} МБ), разделяем на части...")
            elif total_duration > 30.0:
                logger.info(f"Файл слишком длинный ({total_duration:.1f} сек, максимум 30 сек), разделяем на части...")
            
            # Используем уже полученную длительность (определена выше)
            if total_duration <= 0:
                # Если все еще не удалось определить, используем консервативную оценку по размеру
                total_duration = file_size / (10 * 1024)  # 10 КБ/секунда для консервативной оценки
                logger.info(f"Используем оценку длительности: {total_duration:.1f} секунд")
            
            # Оцениваем размер одной секунды аудио
            bytes_per_second = file_size / total_duration
            
            # Вычисляем длительность части, которая будет меньше 1 МБ (с запасом 10%)
            chunk_duration_by_size = (self.max_size * 0.9) / bytes_per_second
            
            # Yandex SpeechKit ограничивает длительность аудио до 30 секунд
            # Используем максимум 25 секунд с запасом
            MAX_CHUNK_DURATION = 25.0
            
            # Выбираем минимальное значение из ограничений по размеру и длительности
            chunk_duration = min(chunk_duration_by_size, MAX_CHUNK_DURATION)
            
            # Проверяем наличие ffmpeg для информационных сообщений
            ffmpeg_available = await self._check_ffmpeg_available()
            if not ffmpeg_available:
                logger.info("ffmpeg недоступен, используем альтернативный метод разделения по байтам")
            
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
                        
                        # Разделяем файл (метод автоматически выберет ffmpeg или альтернативный)
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
                        
                        # Проверяем длительность части (если возможно)
                        chunk_duration_actual = await self._get_audio_duration(chunk_path)
                        if chunk_duration_actual > 30.0:
                            logger.warning(
                                f"Часть {i+1} слишком длинная ({chunk_duration_actual:.1f} сек, "
                                f"максимум 30 сек), пропускаем"
                            )
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
