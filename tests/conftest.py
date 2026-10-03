import os

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://cards:cards@localhost:5432/cards",
)

import uuid

import asyncpg
import pytest
from app.core.config import settings
from app.core.db import async_database_url, close_pool
from app.main import app
from fastapi.testclient import TestClient
from sqlalchemy.engine import make_url


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
async def scratch_database(monkeypatch: pytest.MonkeyPatch):
    """Одноразовая пустая база. `settings.database_url` указывает на неё до конца теста.

    Без Postgres или без права создавать базы тест пропускается.
    """
    url = make_url(async_database_url(settings.database_url))
    name = f"scratch_{uuid.uuid4().hex[:8]}"
    admin_dsn = url.set(drivername="postgresql", database="postgres").render_as_string(
        hide_password=False
    )
    try:
        admin = await asyncpg.connect(admin_dsn)
        await admin.execute(f'CREATE DATABASE "{name}"')
    except Exception as exc:
        pytest.skip(f"postgres unavailable or no right to create a database: {exc}")
    scratch_url = url.set(database=name).render_as_string(hide_password=False)
    monkeypatch.setattr(settings, "database_url", scratch_url)
    await close_pool()
    try:
        yield scratch_url
    finally:
        await close_pool()
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.close()
