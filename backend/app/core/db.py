from collections.abc import AsyncIterator
from functools import lru_cache

from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings
from app.core.orm_events import register_orm_events

register_orm_events()


def _create_engine(url: SecretStr) -> AsyncEngine:
    return create_async_engine(url.get_secret_value(), pool_pre_ping=True)


@lru_cache
def get_engine() -> AsyncEngine:
    """Пул kut_app: внутренний трафик (чтение и запись под RLS)."""
    return _create_engine(get_settings().database_url)


@lru_cache
def get_portal_engine() -> AsyncEngine:
    """Отдельный пул kut_portal: внешние роли, только SELECT (ADR-001 §1, вариант C)."""
    url = get_settings().database_portal_url
    if url is None:
        raise RuntimeError("DATABASE_PORTAL_URL is not configured")
    return _create_engine(url)


@lru_cache
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False)


@lru_cache
def get_portal_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_portal_engine(), expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with get_sessionmaker()() as session:
        yield session


async def get_portal_session() -> AsyncIterator[AsyncSession]:
    async with get_portal_sessionmaker()() as session:
        yield session


async def check_db() -> None:
    async with get_engine().connect() as connection:
        await connection.execute(text("SELECT 1"))


async def dispose_engine() -> None:
    for engine_factory, sessionmaker_factory in (
        (get_engine, get_sessionmaker),
        (get_portal_engine, get_portal_sessionmaker),
    ):
        if engine_factory.cache_info().currsize:
            await engine_factory().dispose()
            engine_factory.cache_clear()
            sessionmaker_factory.cache_clear()
