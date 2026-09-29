import httpx
import pytest
from app.llm.client import LlmClient, LlmError


def _response(status: int, body: dict | None = None) -> httpx.Response:
    return httpx.Response(status, json=body or {}, request=httpx.Request("POST", "http://model/v1"))


def test_client_retries_429_then_returns(monkeypatch: pytest.MonkeyPatch) -> None:
    replies = [
        _response(429),
        _response(200, {"choices": [{"message": {"content": "ok"}}]}),
    ]
    sleeps: list[float] = []

    def fake_post(*_args, **_kwargs):
        response = replies.pop(0)
        response.raise_for_status()
        return response

    monkeypatch.setattr("app.llm.client.httpx.post", fake_post)
    monkeypatch.setattr("app.llm.client.time.sleep", sleeps.append)
    monkeypatch.setattr("app.llm.client.random.random", lambda: 0)
    monkeypatch.setattr("app.llm.client._record_call", lambda **_kwargs: None)

    assert LlmClient().complete("system", "user") == "ok"
    assert len(sleeps) == 1
    assert sleeps[0] > 0


def test_client_does_not_retry_400(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def fake_post(*_args, **_kwargs):
        calls["n"] += 1
        response = _response(400)
        response.raise_for_status()
        return response

    monkeypatch.setattr("app.llm.client.httpx.post", fake_post)
    monkeypatch.setattr("app.llm.client.time.sleep", lambda _seconds: None)
    monkeypatch.setattr("app.llm.client._record_call", lambda **_kwargs: None)

    with pytest.raises(LlmError):
        LlmClient().complete("system", "user")
    assert calls["n"] == 1


def test_complete_sends_schema_without_strict_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict = {}

    def fake_post(*_args, **kwargs):
        seen["json"] = kwargs["json"]
        response = _response(200, {"choices": [{"message": {"content": "{}"}}]})
        response.raise_for_status()
        return response

    monkeypatch.setattr("app.llm.client.httpx.post", fake_post)
    monkeypatch.setattr("app.llm.client._record_call", lambda **_kwargs: None)
    schema = {"title": "CardDraft", "type": "object"}
    assert LlmClient().complete("system", "user", schema=schema) == "{}"
    fmt = seen["json"]["response_format"]
    assert fmt["type"] == "json_schema"
    assert fmt["json_schema"]["strict"] is False
    assert fmt["json_schema"]["schema"] == schema
