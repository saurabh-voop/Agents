"""
Database connection management.
Provides both async (for FastAPI) and sync (for Celery workers) engines.
"""

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, Session
from functools import lru_cache

from core.config import get_settings


@lru_cache()
def get_sync_engine():
    """Synchronous engine for Celery workers and direct queries."""
    settings = get_settings()
    return create_engine(
        settings.database_url_sync,
        pool_size=10,
        max_overflow=20,
        pool_pre_ping=True,
    )


@lru_cache()
def get_async_engine():
    """Async engine for FastAPI endpoints."""
    settings = get_settings()
    return create_async_engine(
        settings.database_url,
        pool_size=10,
        max_overflow=20,
        pool_pre_ping=True,
    )


def get_sync_session() -> Session:
    """Get a synchronous database session."""
    engine = get_sync_engine()
    SessionLocal = sessionmaker(bind=engine)
    return SessionLocal()


async def get_async_session() -> AsyncSession:
    """Get an async database session for FastAPI dependency injection."""
    engine = get_async_engine()
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        yield session
