import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings

_engine: AsyncEngine | None = None
session_factory: async_sessionmaker[AsyncSession] | None = None
_main_loop: asyncio.AbstractEventLoop | None = None


def remember_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _main_loop
    _main_loop = loop


def run_db(coro):
    """Выполнить корутину БД из синхронного кода (поток воркера или sync-ручка)."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        raise RuntimeError("run_db from async context: await the coroutine")
    if _main_loop is not None and _main_loop.is_running():
        return asyncio.run_coroutine_threadsafe(coro, _main_loop).result(timeout=30)
    open_pool()
    return asyncio.run(coro)


def async_database_url(url: str) -> str:
    if url.startswith("postgresql+asyncpg://"):
        return url
    if url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url.removeprefix("postgresql://")
    if url.startswith("postgres://"):
        return "postgresql+asyncpg://" + url.removeprefix("postgres://")
    return url


def open_pool() -> AsyncEngine:
    """Движок и фабрика сессий живут вместе с процессом. Соединение здесь не берётся."""
    global _engine, session_factory
    if _engine is None:
        _engine = create_async_engine(
            async_database_url(settings.database_url),
            pool_pre_ping=True,
            pool_size=10,
        )
        session_factory = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


async def close_pool() -> None:
    global _engine, session_factory
    engine = _engine
    _engine = None
    session_factory = None
    if engine is not None:
        await engine.dispose()


def pool():
    """Пул соединений движка. Это не сессия."""
    if _engine is None:
        raise RuntimeError("pool is not open")
    return _engine.pool


@asynccontextmanager
async def connection() -> AsyncIterator[AsyncConnection]:
    """Одно соединение из пула на операцию, затем оно возвращается."""
    if _engine is None:
        raise RuntimeError("pool is not open")
    engine = _engine
    async with engine.connect() as conn:
        yield conn


async def get_db() -> AsyncIterator[AsyncSession]:
    """Сессия на одну операцию. Зависимость FastAPI с yield, не объект на всё приложение."""
    if session_factory is None:
        raise RuntimeError("pool is not open")
    async with session_factory() as session:
        yield session
