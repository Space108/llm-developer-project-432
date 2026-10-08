import asyncio
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, AbstractContextManager
from contextvars import ContextVar
from typing import Any

import psycopg
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
_sync_stack: ContextVar[tuple[Any, ...]] = ContextVar("sync_stack", default=())


def current_sync_connection():
    """Открытое sync-соединение фикстуры. Поиск видит её незакоммиченные строки."""
    stack = _sync_stack.get()
    return stack[-1] if stack else None


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


def sync_database_url(url: str) -> str:
    """DSN для psycopg (sync-путь каркаса Хекслета)."""
    cleaned = url
    for prefix in ("postgresql+asyncpg://", "postgresql+psycopg://", "postgres://"):
        if cleaned.startswith(prefix):
            cleaned = "postgresql://" + cleaned.removeprefix(prefix)
            break
    return cleaned


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


class _ConnectionCM(AbstractContextManager, AbstractAsyncContextManager):
    """Sync `with` — psycopg (тесты Хекслета). `async with` — AsyncConnection (наш код)."""

    def __init__(self) -> None:
        self._async_cm = None
        self._async_conn: AsyncConnection | None = None
        self._sync_conn: Any = None
        self._ctx_token = None

    def __enter__(self):
        # Схему создают только миграции. Импорт здесь, потому что migrate.py сам берёт db.py.
        from app.core.migrate import apply_migrations_sync, pending_migrations_sync

        self._sync_conn = psycopg.connect(sync_database_url(settings.database_url))
        try:
            if settings.auto_migrate:
                apply_migrations_sync(self._sync_conn)
            else:
                # Без прав на DDL: схему применяют заранее, здесь её только проверяют.
                pending = pending_migrations_sync(self._sync_conn)
                if pending:
                    raise RuntimeError(
                        "не применены миграции: "
                        + ", ".join(pending)
                        + ". Выполните `python -m app.core.migrate`"
                        " (AUTO_MIGRATE=false отключает применение при открытии соединения)"
                    )
        except BaseException:
            self._sync_conn.close()
            self._sync_conn = None
            raise
        self._ctx_token = _sync_stack.set(_sync_stack.get() + (self._sync_conn,))
        return self._sync_conn

    def __exit__(self, exc_type, exc, tb) -> None:
        assert self._sync_conn is not None
        try:
            if exc_type is None:
                self._sync_conn.commit()
            else:
                self._sync_conn.rollback()
        finally:
            if self._ctx_token is not None:
                _sync_stack.reset(self._ctx_token)
                self._ctx_token = None
            self._sync_conn.close()
            self._sync_conn = None

    async def __aenter__(self) -> AsyncConnection:
        if _engine is None:
            raise RuntimeError("pool is not open")
        self._async_cm = _engine.connect()
        self._async_conn = await self._async_cm.__aenter__()
        return self._async_conn

    async def __aexit__(self, exc_type, exc, tb) -> bool | None:
        assert self._async_cm is not None
        try:
            return await self._async_cm.__aexit__(exc_type, exc, tb)
        finally:
            self._async_conn = None
            self._async_cm = None


def connection() -> _ConnectionCM:
    """Одно соединение из пула на операцию, затем оно возвращается."""
    return _ConnectionCM()


async def get_db() -> AsyncIterator[AsyncSession]:
    """Сессия на одну операцию. Зависимость FastAPI с yield, не объект на всё приложение."""
    if session_factory is None:
        raise RuntimeError("pool is not open")
    async with session_factory() as session:
        yield session
