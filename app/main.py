import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.db import close_pool, open_pool, remember_loop
from app.core.logging import RequestLogMiddleware, configure_logging
from app.routers.cards import router as cards_router
from app.routers.documents import router as documents_router
from app.routers.generate import router as generate_router
from app.routers.health import router as health_router
from app.routers.jobs import router as jobs_router
from app.routers.workflows import router as workflows_router


@asynccontextmanager
async def lifespan(_app: FastAPI):
    configure_logging()
    open_pool()
    remember_loop(asyncio.get_running_loop())
    yield
    await close_pool()


app = FastAPI(title="ИИ-генератор карточки товара", lifespan=lifespan)
app.add_middleware(RequestLogMiddleware)
app.include_router(health_router)
app.include_router(cards_router)
app.include_router(jobs_router)
app.include_router(documents_router)
app.include_router(generate_router)
app.include_router(workflows_router)
