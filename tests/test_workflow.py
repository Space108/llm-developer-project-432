import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from app.core.db import close_pool, connection, open_pool
from app.core.migrate import apply_migrations
from app.repositories.jobs import insert_job, load_job
from app.schemas.cards import CardDraft, CritiqueReport, SupplierFacts
from app.services import pipeline
from app.temporal.activities import (
    critique_activity,
    extract_activity,
    generate_activity,
    set_status_activity,
)
from app.temporal.workflows import CardWorkflow
from sqlalchemy import text
from temporalio.client import Client
from temporalio.worker import Worker


def _install_model(monkeypatch: pytest.MonkeyPatch, calls: list[str]) -> None:
    facts = SupplierFacts(product_name="Блендер", characteristics={"Мощность": "800 Вт"})
    draft = CardDraft(
        title="Блендер 800 Вт",
        description="Погружной блендер с металлической ножкой.",
        characteristics={"Мощность": "800 Вт"},
        benefits=["Металлическая ножка"],
    )
    report = CritiqueReport(verdict="approve")

    def complete(_self, system: str, _user: str, **_kwargs: object) -> str:
        if "извлекатель" in system:
            calls.append("extract")
            return facts.model_dump_json()
        if "копирайтер" in system:
            calls.append("generate")
            return draft.model_dump_json()
        calls.append("critique")
        return report.model_dump_json()

    monkeypatch.setattr(pipeline.LlmClient, "complete", complete)


async def _wait_status(job_id: str, status: str, timeout: float = 20) -> dict:
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = await load_job(job_id)
        if last is not None and last["status"] == status:
            return last
        await asyncio.sleep(0.1)
    raise AssertionError(f"status {status!r} not reached, last={last}")


async def test_signal_finishes_confirmation(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    _install_model(monkeypatch, calls)
    open_pool()
    client = None
    try:
        try:
            async with connection() as conn:
                await conn.execute(text("SELECT 1"))
            client = await asyncio.wait_for(Client.connect("localhost:7233"), timeout=3)
        except Exception as exc:
            pytest.skip(f"postgres or temporal unavailable: {exc}")
        await apply_migrations()
        job_id, created = await insert_job("текст блендера", None)
        assert created is True
        queue = f"card-test-{job_id}"
        with ThreadPoolExecutor(max_workers=4) as executor:
            worker = Worker(
                client,
                task_queue=queue,
                workflows=[CardWorkflow],
                activities=[
                    extract_activity,
                    generate_activity,
                    critique_activity,
                    set_status_activity,
                ],
                activity_executor=executor,
            )
            async with worker:
                handle = await client.start_workflow(
                    CardWorkflow.run,
                    args=[job_id, "текст блендера", 3],
                    id=job_id,
                    task_queue=queue,
                )
                waiting = await _wait_status(job_id, "awaiting_confirmation")
                assert waiting["result"].title == "Блендер 800 Вт"
                assert await handle.query(CardWorkflow.current_status) == "awaiting_confirmation"
                await handle.signal(CardWorkflow.approve)
                await asyncio.wait_for(handle.result(), timeout=20)
        finished = await load_job(job_id)
        assert finished is not None
        assert finished["status"] == "approved"
        assert calls == ["extract", "generate", "critique"]
    finally:
        await close_pool()


def _install_blocking_generate(
    monkeypatch: pytest.MonkeyPatch,
    calls: list[str],
    entered: threading.Event,
    release: threading.Event,
) -> None:
    facts = SupplierFacts(product_name="Блендер", characteristics={"Мощность": "800 Вт"})
    draft = CardDraft(
        title="Блендер 800 Вт",
        description="Погружной блендер с металлической ножкой.",
        characteristics={"Мощность": "800 Вт"},
        benefits=["Металлическая ножка"],
    )
    report = CritiqueReport(verdict="approve")

    def complete(_self, system: str, _user: str, **_kwargs: object) -> str:
        if "извлекатель" in system:
            calls.append("extract")
            return facts.model_dump_json()
        if "копирайтер" in system:
            calls.append("generate")
            if calls.count("generate") == 1:
                entered.set()
                release.wait(15)
            return draft.model_dump_json()
        calls.append("critique")
        return report.model_dump_json()

    monkeypatch.setattr(pipeline.LlmClient, "complete", complete)


async def test_new_worker_does_not_repeat_finished_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    entered = threading.Event()
    release = threading.Event()
    _install_blocking_generate(monkeypatch, calls, entered, release)
    open_pool()
    try:
        try:
            async with connection() as conn:
                await conn.execute(text("SELECT 1"))
            client = await asyncio.wait_for(Client.connect("localhost:7233"), timeout=3)
        except Exception as exc:
            pytest.skip(f"postgres or temporal unavailable: {exc}")
        await apply_migrations()
        job_id, _created = await insert_job("текст блендера", None)
        queue = f"card-test-{job_id}"
        activities = [
            extract_activity,
            generate_activity,
            critique_activity,
            set_status_activity,
        ]

        def make_worker(executor: ThreadPoolExecutor) -> Worker:
            return Worker(
                client,
                task_queue=queue,
                workflows=[CardWorkflow],
                activities=activities,
                activity_executor=executor,
            )

        with ThreadPoolExecutor(max_workers=4) as executor:
            worker = make_worker(executor)
            run_task = asyncio.create_task(worker.run())
            handle = await client.start_workflow(
                CardWorkflow.run,
                args=[job_id, "текст блендера", 3],
                id=job_id,
                task_queue=queue,
            )
            started = await asyncio.to_thread(entered.wait, 15)
            assert started, calls

            async def _release() -> None:
                await asyncio.sleep(0.2)
                release.set()

            releaser = asyncio.create_task(_release())
            await asyncio.wait_for(worker.shutdown(), timeout=15)
            await run_task
            await releaser
        after_kill = list(calls)
        assert after_kill == ["extract", "generate"]

        with ThreadPoolExecutor(max_workers=4) as executor:
            async with make_worker(executor):
                await _wait_status(job_id, "awaiting_confirmation", timeout=30)
                assert calls.count("extract") == 1
                assert calls.count("critique") == 1
                await handle.signal(CardWorkflow.reject)
                await asyncio.wait_for(handle.result(), timeout=20)
        finished = await load_job(job_id)
        assert finished is not None
        assert finished["status"] == "rejected"
        assert calls.count("extract") == 1
    finally:
        release.set()
        await close_pool()
