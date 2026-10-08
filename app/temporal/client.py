import asyncio

from temporalio.client import Client

from app.core.config import settings

# Клиент держит соединение с сервером, поэтому его создают один раз на процесс, а не на запрос.
# Он привязан к циклу событий и адресу: при смене любого из них подключаемся заново.
_cached: tuple[asyncio.AbstractEventLoop, str, Client] | None = None
_locks: dict[int, asyncio.Lock] = {}


def _lock_for(loop: asyncio.AbstractEventLoop) -> asyncio.Lock:
    lock = _locks.get(id(loop))
    if lock is None:
        lock = _locks[id(loop)] = asyncio.Lock()
    return lock


def _reusable(loop: asyncio.AbstractEventLoop, host: str) -> Client | None:
    if _cached is None:
        return None
    cached_loop, cached_host, client = _cached
    if cached_loop is loop and cached_host == host and not loop.is_closed():
        return client
    return None


async def connect() -> Client:
    global _cached
    loop = asyncio.get_running_loop()
    host = settings.temporal_host
    client = _reusable(loop, host)
    if client is not None:
        return client
    async with _lock_for(loop):
        client = _reusable(loop, host)
        if client is not None:
            return client
        client = await Client.connect(host)
        _cached = (loop, host, client)
        return client
