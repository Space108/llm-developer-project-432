import uuid

import pytest
from app.core.db import close_pool, connection, open_pool
from app.core.migrate import apply_migrations
from app.repositories.jobs import insert_job, load_job, update_job_status
from app.schemas.cards import CardDraft, CritiqueReport, SupplierFacts
from fastapi.testclient import TestClient
from sqlalchemy import text

KEY = "step3-idempotency"


async def _postgres_or_skip() -> None:
    try:
        async with connection() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:
        pytest.skip(f"postgres unavailable: {exc}")


async def test_repository_keeps_one_job_for_the_same_key() -> None:
    open_pool()
    try:
        await _postgres_or_skip()
        await apply_migrations()
        async with connection() as conn:
            await conn.execute(text("DELETE FROM jobs WHERE idempotency_key = :key"), {"key": KEY})
            await conn.commit()
        job_id, created = await insert_job("текст блендера", KEY)
        assert created is True
        again, created_again = await insert_job("текст блендера", KEY)
        assert created_again is False
        assert again == job_id
        await update_job_status(job_id, "generating", bump_attempts=True)
        loaded = await load_job(job_id)
        assert loaded is not None
        assert loaded["status"] == "generating"
        assert loaded["attempts"] == 1
    finally:
        await close_pool()


def test_same_key_starts_one_workflow(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    started: list[str] = []

    class _Client:
        async def start_workflow(self, *_args, **kwargs) -> None:
            started.append(kwargs["id"])

    async def fake_connect():
        return _Client()

    monkeypatch.setattr("app.routers.jobs.connect", fake_connect)
    key = f"step3-http-{uuid.uuid4().hex}"
    body = {"supplier_text": "блендер 800 Вт"}
    headers = {"Idempotency-Key": key}
    try:
        first = client.post("/jobs", json=body, headers=headers)
    except OSError as exc:
        pytest.skip(f"postgres unavailable: {exc}")
    if first.status_code == 500:
        pytest.skip("postgres unavailable")
    second = client.post("/jobs", json=body, headers=headers)
    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["id"] == second.json()["id"]
    assert first.json()["status"] == "pending"
    assert started == [first.json()["id"]]


def test_sync_handler_returns_card(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    draft = CardDraft(
        title="Блендер 800 Вт",
        description="Погружной блендер.",
        characteristics={"Мощность": "800 Вт"},
        benefits=["Металлическая ножка"],
    )

    def fake_run(_text: str, max_attempts: int = 3):
        return draft, 1, "approved"

    monkeypatch.setattr("app.routers.cards.run_pipeline", fake_run)
    response = client.post("/cards", json={"supplier_text": "мощность 800 Вт"})
    assert response.status_code == 200
    assert response.json()["title"] == "Блендер 800 Вт"
    assert response.json()["characteristics"] == {"Мощность": "800 Вт"}


def test_sync_handler_on_sparse_text(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    facts = SupplierFacts(product_name="Товар", missing_fields=["description"])
    draft = CardDraft(
        title="Товар",
        description="Коротко.",
        missing_fields=["description"],
        confidence=0.2,
    )
    script = [facts, draft, CritiqueReport(verdict="approve")]
    calls = {"n": 0}

    def complete(_self, _system: str, _user: str, **_kwargs: object) -> str:
        item = script[calls["n"]]
        calls["n"] += 1
        return item.model_dump_json()

    monkeypatch.setattr("app.services.pipeline.LlmClient.complete", complete)
    monkeypatch.setattr("app.services.pipeline.settings.confidence_threshold", 0.5)
    response = client.post("/cards", json={"supplier_text": "мало данных"})
    assert response.status_code == 200
    body = response.json()
    assert body["missing_fields"]
    assert body["confidence"] < 0.5
