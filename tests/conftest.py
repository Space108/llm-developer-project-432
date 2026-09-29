import os

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://cards:cards@localhost:5432/cards",
)

import pytest
from app.main import app
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client
