import pytest
from app.core.db import get_db, pool
from app.main import app
from fastapi.testclient import TestClient


class _Result:
    def __init__(self, row):
        self._row = row

    def first(self):
        return self._row


class _Session:
    def __init__(self, row):
        self._row = row
        self.calls = 0

    async def execute(self, _statement):
        self.calls += 1
        if isinstance(self._row, Exception):
            raise self._row
        return _Result(self._row)


def test_liveness_ignores_database(client: TestClient) -> None:
    async def boom():
        raise RuntimeError("db down")
        yield None

    app.dependency_overrides[get_db] = boom
    try:
        response = client.get("/health")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_fails_without_database(client: TestClient) -> None:
    broken = _Session(RuntimeError("db down"))

    async def override():
        yield broken

    app.dependency_overrides[get_db] = override
    try:
        response = client.get("/health/ready")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 503


def test_readiness_shows_active_vector(client: TestClient) -> None:
    async def override():
        yield _Session(("vector",))

    app.dependency_overrides[get_db] = override
    try:
        response = client.get("/health/ready")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "vector": "active"}


def test_each_operation_gets_its_own_session(client: TestClient) -> None:
    seen: list[object] = []

    async def override():
        marker = object()
        seen.append(marker)
        yield _Session(("vector",))

    app.dependency_overrides[get_db] = override
    try:
        first = client.get("/health/ready")
        second = client.get("/health/ready")
    finally:
        app.dependency_overrides.clear()
    assert first.status_code == 200
    assert second.status_code == 200
    assert len(seen) == 2
    assert seen[0] is not seen[1]


def test_pool_closes_with_the_process() -> None:
    with TestClient(app):
        pool()
    with pytest.raises(RuntimeError, match="pool is not open"):
        pool()
