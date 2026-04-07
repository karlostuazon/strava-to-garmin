import logging
from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    String,
    Text,
    BigInteger,
    UniqueConstraint,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

logger = logging.getLogger(__name__)

engine = create_async_engine(settings.DATABASE_URL, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class StravaToken(Base):
    __tablename__ = "strava_tokens"

    id = Column(Integer, primary_key=True)
    access_token = Column(String(255), nullable=False)
    refresh_token = Column(String(255), nullable=False)
    expires_at = Column(Integer, nullable=False)
    updated_at = Column(
        DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )


class GarminToken(Base):
    __tablename__ = "garmin_tokens"

    id = Column(Integer, primary_key=True)
    token_json = Column(Text, nullable=False)
    updated_at = Column(
        DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )


class SyncedActivity(Base):
    __tablename__ = "synced_activities"

    id = Column(Integer, primary_key=True)
    strava_activity_id = Column(BigInteger, nullable=False)
    activity_name = Column(String(500), default="")
    activity_type = Column(String(100), default="")
    file_format = Column(String(10), default="")
    garmin_activity_id = Column(String(100), default="")
    status = Column(String(50), nullable=False, default="pending")
    error_message = Column(Text, default="")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        UniqueConstraint("strava_activity_id", name="uq_strava_activity_id"),
    )


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database initialized")


async def get_session() -> AsyncSession:
    async with async_session() as session:
        yield session
