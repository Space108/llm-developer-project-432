import asyncio
import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from app.core.db import close_pool, connection, open_pool
from app.core.migrate import apply_migrations
from app.repositories.documents import insert_document, load_document
from app.temporal.activities import index_document_activity, parse_document_activity
from app.temporal.workflows import DocumentWorkflow
from openpyxl import Workbook
from sqlalchemy import text
from temporalio.client import Client
from temporalio.worker import Worker


async def _wait_status(document_id: str, status: str, timeout: float = 20) -> dict:
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = await load_document(document_id)
        if last is not None and last["status"] == status:
            return last
        await asyncio.sleep(0.1)
    raise AssertionError(f"status {status!r} not reached, last={last}")


async def test_document_workflow_indexes_spec(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document_id = ""
    async def _no_vectors(_document_id: str) -> int:
        return 0

    monkeypatch.setattr("app.services.index.index_document", _no_vectors)
    open_pool()
    try:
        try:
            async with connection() as conn:
                await conn.execute(text("SELECT 1"))
            client = await asyncio.wait_for(Client.connect("localhost:7233"), timeout=3)
        except Exception as exc:
            pytest.skip(f"postgres or temporal unavailable: {exc}")
        await apply_migrations()
        path = tmp_path / "spec.xlsx"
        book = Workbook()
        sheet = book.active
        sheet.title = "Спецификация"
        sheet.append(["Артикул", "мощность", "чаша"])
        sheet.append(["BLD-800", "800 Вт", "1.5 л"])
        book.save(path)
        book.close()
        document_id = "doc" + secrets.token_hex(4)
        async with connection() as conn:
            await conn.execute(
                text("DELETE FROM fragments WHERE document_id = :id"),
                {"id": document_id},
            )
            await conn.execute(text("DELETE FROM documents WHERE id = :id"), {"id": document_id})
            await conn.commit()
        await insert_document(document_id, "spec.xlsx", "hash-" + document_id, str(path))
        queue = f"card-test-{document_id}"
        with ThreadPoolExecutor(max_workers=4) as executor:
            worker = Worker(
                client,
                task_queue=queue,
                workflows=[DocumentWorkflow],
                activities=[parse_document_activity, index_document_activity],
                activity_executor=executor,
            )
            async with worker:
                await client.start_workflow(
                    DocumentWorkflow.run,
                    args=[document_id],
                    id=document_id,
                    task_queue=queue,
                )
                finished = await _wait_status(document_id, "проиндексирован")
        assert finished["error"] is None
        async with connection() as conn:
            result = await conn.execute(
                text("SELECT page, section, text FROM fragments WHERE document_id = :id"),
                {"id": document_id},
            )
            row = result.mappings().one()
        assert row["page"] == 1
        assert row["section"] == "Спецификация"
        assert row["text"] == "BLD-800: мощность 800 Вт; чаша 1.5 л"
    finally:
        if document_id:
            async with connection() as conn:
                await conn.execute(
                    text("DELETE FROM fragments WHERE document_id = :id"),
                    {"id": document_id},
                )
                await conn.execute(
                    text("DELETE FROM documents WHERE id = :id"),
                    {"id": document_id},
                )
                await conn.commit()
        await close_pool()
