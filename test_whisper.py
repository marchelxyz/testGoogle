#!/usr/bin/env python3
"""
Простой тест для проверки работы OpenAI Whisper API
"""
import asyncio
import os
from transcription import TranscriptionService
from config import Config

async def test_whisper_api():
    """Тестирование OpenAI Whisper API"""
    
    # Проверяем конфигурацию
    try:
        Config.validate()
        print("✅ Конфигурация прошла валидацию")
    except Exception as e:
        print(f"❌ Ошибка конфигурации: {e}")
        return
    
    # Создаем сервис транскрибации
    transcription_service = TranscriptionService()
    
    # Проверяем наличие API ключа
    if not transcription_service.api_key:
        print("❌ OpenAI API ключ не найден")
        return
    
    print("✅ OpenAI API ключ найден")
    
    # Ищем тестовый аудиофайл
    test_audio_path = "test_audio.ogg"  # Укажите путь к тестовому файлу
    
    if not os.path.exists(test_audio_path):
        print(f"⚠️ Тестовый аудиофайл не найден: {test_audio_path}")
        print("Создайте тестовый аудиофайл или укажите правильный путь")
        return
    
    try:
        print(f"🎵 Начинаем транскрибацию файла: {test_audio_path}")
        
        # Получаем размер файла
        file_size = os.path.getsize(test_audio_path)
        print(f"📊 Размер файла: {file_size / 1024:.1f} КБ")
        
        # Проверяем лимит размера
        if file_size > transcription_service.max_size:
            print(f"❌ Файл слишком большой: {file_size / (1024*1024):.2f} МБ")
            print(f"Максимум: {transcription_service.max_size / (1024*1024):.0f} МБ")
            return
        
        # Выполняем транскрибацию
        result = await transcription_service.transcribe_voice(test_audio_path)
        
        print(f"✅ Транскрибация завершена успешно!")
        print(f"📝 Результат: {result}")
        
    except Exception as e:
        print(f"❌ Ошибка при транскрибации: {e}")

if __name__ == "__main__":
    print("🧪 Тестирование OpenAI Whisper API")
    print("=" * 50)
    
    # Инструкции
    print("Перед запуском теста:")
    print("1. Установите OPENAI_API_KEY в переменных окружения")
    print("2. Создайте тестовый аудиофайл (например, test_audio.ogg)")
    print("3. Убедитесь, что файл не превышает 25 МБ")
    print()
    
    asyncio.run(test_whisper_api())
