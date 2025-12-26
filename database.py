"""База данных для хранения событий и уведомлений"""
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base, Mapped, mapped_column
from sqlalchemy import String, DateTime, Integer, Boolean, Text
from datetime import datetime
from typing import Optional
from config import Config

Base = declarative_base()

class CalendarEvent(Base):
    """Модель события календаря"""
    __tablename__ = "calendar_events"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[str] = mapped_column(String, unique=True, nullable=False)  # ID из Яндекс Календаря
    summary: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    start_datetime: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    end_datetime: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    notified_15min: Mapped[bool] = mapped_column(Boolean, default=False)
    notified_60min: Mapped[bool] = mapped_column(Boolean, default=False)
    telegram_user_id: Mapped[int] = mapped_column(Integer, nullable=False)

class Notification(Base):
    """Модель уведомления"""
    __tablename__ = "notifications"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(Integer, nullable=False)
    notification_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    sent: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class UserCredentials(Base):
    """Модель учетных данных пользователя для Яндекс Календаря"""
    __tablename__ = "user_credentials"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_user_id: Mapped[int] = mapped_column(Integer, unique=True, nullable=False)
    yandex_user: Mapped[str] = mapped_column(String, nullable=False)  # Email пользователя
    yandex_password: Mapped[str] = mapped_column(String, nullable=False)  # Пароль приложения
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

# Создание движка и сессии
engine = create_async_engine(Config.DATABASE_URL, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

async def init_db():
    """Инициализация базы данных"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

async def get_session() -> AsyncSession:
    """Получение сессии базы данных"""
    async with async_session() as session:
        yield session
