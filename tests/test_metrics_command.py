import pytest
from app.commands import metrics
from app.core.db import close_pool, connection, open_pool
from app.llm.parse import ModelResponseError


async def test_flaky_model_answer_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def flaky(filename: str, expected: dict) -> dict:
        calls.append(filename)
        if len(calls) < 3:
            raise ModelResponseError("confidence: 85 больше 1")
        return {"filename": filename}

    monkeypatch.setattr(metrics, "_eval_one", flaky)
    row = await metrics._eval_with_retry("doc.pdf", {})
    assert row == {"filename": "doc.pdf"}
    assert len(calls) == 3


async def test_last_model_error_is_not_hidden(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def always_bad(filename: str, expected: dict) -> dict:
        calls.append(filename)
        raise ModelResponseError("ответ не разобран")

    monkeypatch.setattr(metrics, "_eval_one", always_bad)
    with pytest.raises(ModelResponseError, match="ответ не разобран"):
        await metrics._eval_with_retry("doc.pdf", {})
    assert len(calls) == metrics.EVAL_ATTEMPTS


async def test_other_errors_are_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def broken(filename: str, expected: dict) -> dict:
        calls.append(filename)
        raise RuntimeError("база недоступна")

    monkeypatch.setattr(metrics, "_eval_one", broken)
    with pytest.raises(RuntimeError, match="база недоступна"):
        await metrics._eval_with_retry("doc.pdf", {})
    assert len(calls) == 1


async def test_existing_document_gets_missing_vectors(
    scratch_database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Документ найден по имени, но разобран без векторов: команда досчитывает их."""
    indexed: list[str] = []

    async def fake_index(document_id: str) -> int:
        indexed.append(document_id)
        return 0

    monkeypatch.setattr("app.temporal.activities.index_document_activity", fake_index)
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO documents (id, filename, status, path) "
            "VALUES ('d1', 'a.pdf', 'x', 'data/uploads/a.pdf')"
        )
    open_pool()
    try:
        assert await metrics._ensure_document("a.pdf") == "d1"
    finally:
        await close_pool()
    assert indexed == ["d1"]
