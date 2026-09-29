import asyncio
import re
import secrets
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from app.core.config import settings
from app.core.db import close_pool, connection, open_pool
from app.core.migrate import apply_migrations
from app.repositories.documents import insert_document, load_document, update_document_status
from app.repositories.jobs import insert_generation_job, load_job, update_job_status
from app.repositories.search import write_embeddings
from app.schemas.cards import CardDraft, CritiqueReport, SourceRef
from app.services import pipeline
from app.services.retrieve import retrieve_context
from app.temporal.activities import (
    critique_context_activity,
    empty_context_card_activity,
    filter_output_activity,
    generate_context_activity,
    index_document_activity,
    prepare_document_activity,
    search_context_activity,
    set_status_activity,
    verify_citations_activity,
)
from app.temporal.workflows import CardWorkflow
from sqlalchemy import text
from temporalio.client import Client
from temporalio.worker import Worker


def _axis(index: int) -> list[float]:
    vector = [0.0] * settings.embedding_dimensions
    vector[index] = 1.0
    return vector


async def _postgres_or_skip() -> None:
    try:
        async with connection() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:
        pytest.skip(f"postgres unavailable: {exc}")


async def _drop(document_id: str) -> None:
    async with connection() as conn:
        await conn.execute(
            text("DELETE FROM fragments WHERE document_id = :id"),
            {"id": document_id},
        )
        await conn.execute(text("DELETE FROM documents WHERE id = :id"), {"id": document_id})
        await conn.commit()


async def _fragment(document_id: str, fragment_id: str, body: str) -> None:
    async with connection() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO fragments (
                    id, document_id, page, section, article, brand, text, position
                )
                VALUES (
                    :id, :document_id, 1, 'Спецификация', '', '', :text, 0
                )
                """
            ),
            {"id": fragment_id, "document_id": document_id, "text": body},
        )
        await conn.commit()


async def test_empty_hint_searches_by_the_document_title(monkeypatch: pytest.MonkeyPatch) -> None:
    document_id = "gen" + secrets.token_hex(4)
    kettle_id = document_id + "k"
    other_id = document_id + "o"

    def fake_embed(query: str) -> list[float]:
        if "чайник" in query:
            return _axis(0)
        return _axis(1)

    monkeypatch.setattr("app.services.embeddings.embed_query", fake_embed)
    open_pool()
    try:
        await _postgres_or_skip()
        await apply_migrations()
        await _drop(document_id)
        try:
            await insert_document(document_id, "чайник.xlsx", "hash-" + document_id, "data/x")
            await update_document_status(document_id, "проиндексирован")
            await _fragment(document_id, kettle_id, "чайник 1.7 л")
            await _fragment(document_id, other_id, "мощность 800 Вт")
            await write_embeddings([(kettle_id, _axis(0)), (other_id, _axis(1))])
            built = await retrieve_context([document_id], "")
            assert [item.id for item in built.built.fragments] == [kettle_id]
            assert built.built.size == len(built.built.text)
            assert built.built.size > 0
            assert f"chunk_id: {kettle_id}" in built.built.text
            assert built.blocked is False
        finally:
            await _drop(document_id)
    finally:
        await close_pool()


def _install_model(monkeypatch: pytest.MonkeyPatch) -> None:
    def complete(_self, system: str, user: str, **_kwargs: object) -> str:
        if "копирайтер" in system:
            found = re.search(r"chunk_id: (\S+)", user)
            chunk_id = found.group(1) if found else "missing"
            draft = CardDraft(
                title="Блендер 800 Вт",
                description="Мощность 800 Вт.",
                characteristics={"Мощность": "800 Вт"},
                benefits=["Металлическая ножка"],
                sources=[SourceRef(chunk_id=chunk_id, page=1, section="Спецификация")],
                missing_fields=["гарантия"],
                confidence=0.4,
            )
            return draft.model_dump_json()
        return CritiqueReport(verdict="approve").model_dump_json()

    monkeypatch.setattr(pipeline.LlmClient, "complete", complete)


async def _wait_status(job_id: str, status: str, timeout: float = 30) -> dict:
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = await load_job(job_id)
        if last is not None and last["status"] == status:
            return last
        await asyncio.sleep(0.1)
    raise AssertionError(f"status {status!r} not reached, last={last}")


async def test_generation_searches_checks_links_and_approves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document_id = "gen" + secrets.token_hex(4)
    power_id = document_id + "p"
    other_id = document_id + "o"
    statuses: list[str] = []

    def fake_embed(query: str) -> list[float]:
        if "мощность" in query:
            return _axis(0)
        return _axis(1)

    def fake_documents(_texts: list[str]) -> list[list[float]]:
        raise AssertionError("модель эмбеддингов в тесте не грузится")

    async def spy(job_id: str, status: str, **kwargs: object) -> None:
        statuses.append(status)
        await update_job_status(job_id, status, **kwargs)

    monkeypatch.setattr("app.services.embeddings.embed_query", fake_embed)
    monkeypatch.setattr("app.services.embeddings.embed_documents", fake_documents)
    monkeypatch.setattr("app.temporal.activities.update_job_status", spy)
    _install_model(monkeypatch)
    open_pool()
    job_id = ""
    try:
        try:
            async with connection() as conn:
                await conn.execute(text("SELECT 1"))
            client = await asyncio.wait_for(Client.connect("localhost:7233"), timeout=3)
        except Exception as exc:
            pytest.skip(f"postgres or temporal unavailable: {exc}")
        await apply_migrations()
        await _drop(document_id)
        try:
            await insert_document(document_id, "passport.pdf", "hash-" + document_id, "data/x")
            await update_document_status(document_id, "проиндексирован")
            await _fragment(document_id, power_id, "мощность 800 Вт")
            await _fragment(document_id, other_id, "гарантия два года")
            await write_embeddings([(power_id, _axis(0)), (other_id, _axis(1))])
            job_id = await insert_generation_job([document_id], "мощность")
            queue = f"card-test-{job_id}"
            with ThreadPoolExecutor(max_workers=4) as executor:
                worker = Worker(
                    client,
                    task_queue=queue,
                    workflows=[CardWorkflow],
                    activities=[
                        prepare_document_activity,
                        index_document_activity,
                        search_context_activity,
                        empty_context_card_activity,
                        generate_context_activity,
                        verify_citations_activity,
                        critique_context_activity,
                        filter_output_activity,
                        set_status_activity,
                    ],
                    activity_executor=executor,
                )
                async with worker:
                    handle = await client.start_workflow(
                        CardWorkflow.run,
                        args=[job_id, "", 3, [document_id], "мощность"],
                        id=job_id,
                        task_queue=queue,
                    )
                    waiting = await _wait_status(job_id, "ожидание")
                    assert waiting["result"].sources[0].chunk_id == power_id
                    assert waiting["result"].confidence < 1
                    assert "гарантия" in waiting["result"].missing_fields
                    assert await handle.query(CardWorkflow.current_status) == "ожидание"
                    stored = await load_document(document_id)
                    assert stored is not None
                    assert stored["status"] == "проиндексирован"
                    await handle.signal(CardWorkflow.approve)
                    await asyncio.wait_for(handle.result(), timeout=20)
            finished = await load_job(job_id)
            assert finished is not None
            assert finished["status"] == "согласовано"
            assert finished["result"].sources[0].chunk_id == power_id
            assert statuses == [
                "разбор",
                "индексация",
                "поиск",
                "генерация",
                "проверка",
                "ожидание",
                "согласовано",
            ]
        finally:
            if job_id:
                async with connection() as conn:
                    await conn.execute(text("DELETE FROM jobs WHERE id = :id"), {"id": job_id})
                    await conn.commit()
            await _drop(document_id)
    finally:
        await close_pool()
