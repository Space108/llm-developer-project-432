"""Чтение задачи и документа: база первая, словарь раннего шага — запасной путь."""

from pathlib import Path

import pytest
from app.repositories.documents import Document, documents
from app.repositories.jobs import Job, jobs
from app.routers import documents as documents_router
from app.routers import jobs as jobs_router


async def _database_down(*_args: object, **_kwargs: object):
    raise ConnectionRefusedError("база не отвечает")


@pytest.fixture
def memory_job():
    job = Job(job_id="memory-job", document_ids=[], product_hint="", status="running")
    jobs[job.job_id] = job
    yield job
    jobs.pop(job.job_id, None)


@pytest.fixture
def memory_document():
    document = Document(document_id="memory-doc", filename="a.pdf", path=Path("a.pdf"))
    documents[document.document_id] = document
    yield document
    documents.pop(document.document_id, None)


def test_job_in_memory_is_read_when_the_database_is_down(
    client, memory_job, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(jobs_router, "load_job", _database_down)
    response = client.get("/jobs/memory-job")
    assert response.status_code == 200
    assert response.json()["status"] == "running"


def test_database_wins_over_memory_when_it_has_the_job(
    client, memory_job, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def stored(_job_id: str) -> dict:
        return {"status": "completed", "attempts": 2, "result": None, "error": None}

    monkeypatch.setattr(jobs_router, "load_job", stored)
    body = client.get("/jobs/memory-job").json()
    assert body["status"] == "completed"
    assert body["attempts"] == 2


def test_unknown_job_with_the_database_down_is_not_hidden(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Нет ни в базе, ни в памяти: ошибка базы остаётся ошибкой, а не превращается в 404."""
    monkeypatch.setattr(jobs_router, "load_job", _database_down)
    with pytest.raises(ConnectionRefusedError):
        client.get("/jobs/nobody")


def test_document_in_memory_is_read_when_the_database_is_down(
    client, memory_document, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(documents_router, "load_document", _database_down)
    response = client.get("/documents/memory-doc")
    assert response.status_code == 200
    assert response.json()["document_id"] == "memory-doc"
