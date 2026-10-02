import pytest
from app.commands import metrics
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
