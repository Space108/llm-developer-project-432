import pytest
from app.repositories.documents import documents
from app.repositories.jobs import jobs
from fastapi.testclient import TestClient
from temporalio.service import RPCError, RPCStatusCode


def setup_function() -> None:
    documents.clear()
    jobs.clear()


def test_generate_card_returns_identifier(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _skip_storage(monkeypatch)
    uploaded = client.post(
        "/documents/",
        files={"file": ("passport.pdf", b"power 800 W", "application/pdf")},
    )
    assert uploaded.status_code == 202
    document_id = uploaded.json()["document_id"]
    assert uploaded.json()["status"] == "новый"
    captured: dict = {}

    class _Client:
        async def start_workflow(self, *_args, **kwargs) -> None:
            captured["args"] = kwargs["args"]
            captured["id"] = kwargs["id"]

    async def fake_connect():
        return _Client()

    async def load(requested: str):
        if requested == document_id:
            return {
                "id": document_id,
                "filename": "passport.pdf",
                "status": "новый",
                "error": None,
                "path": "x",
            }
        return None

    async def insert(document_ids: list[str], product_hint: str) -> str:
        captured["document_ids"] = document_ids
        captured["hint"] = product_hint
        return "job-ctx"

    monkeypatch.setattr("app.routers.generate.load_document", load)
    monkeypatch.setattr("app.routers.generate.insert_generation_job", insert)
    monkeypatch.setattr("app.routers.generate.connect", fake_connect)
    created = client.post(
        "/generate-card",
        json={"document_ids": [document_id], "product_hint": "блендер"},
    )
    assert created.status_code == 202
    body = created.json()
    assert body["job_id"] == "job-ctx"
    assert body["status"] == "pending"
    assert captured["id"] == "job-ctx"
    assert captured["args"][:5] == ["job-ctx", "", 3, [document_id], "блендер"]
    assert captured["args"][5].startswith("req_")
    assert captured["document_ids"] == [document_id]
    assert captured["hint"] == "блендер"


def test_upload_rejects_txt(client: TestClient) -> None:
    response = client.post(
        "/documents/",
        files={"file": ("passport.txt", b"power 800 W", "text/plain")},
    )
    assert response.status_code == 400


def test_upload_rejects_empty_file(client: TestClient) -> None:
    response = client.post(
        "/documents/",
        files={"file": ("passport.pdf", b"", "application/pdf")},
    )
    assert response.status_code == 400


def test_upload_rejects_oversized_file(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.routers.documents.settings.max_upload_bytes", 4)
    response = client.post(
        "/documents/",
        files={"file": ("passport.pdf", b"12345", "application/pdf")},
    )
    assert response.status_code == 400


def test_same_file_returns_existing_document(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started: list[str] = []

    async def existing(*_args, **_kwargs):
        return {"id": "doc-1", "status": "проиндексирован", "error": None}

    class _Client:
        async def start_workflow(self, *_args, **kwargs) -> None:
            started.append(kwargs["id"])

    async def fake_connect():
        return _Client()

    monkeypatch.setattr("app.routers.documents.find_document_by_hash", existing)
    monkeypatch.setattr("app.routers.documents.connect", fake_connect)
    response = client.post(
        "/documents/",
        files={"file": ("spec.xlsx", b"same-bytes", "application/vnd.ms-excel")},
    )
    assert response.status_code == 202
    assert response.json()["document_id"] == "doc-1"
    assert started == []


def test_unknown_document(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def missing(*_args, **_kwargs):
        return None

    monkeypatch.setattr("app.routers.generate.load_document", missing)
    response = client.post(
        "/generate-card",
        json={"document_ids": ["missing"], "product_hint": ""},
    )
    assert response.status_code == 404


def _signal_client(monkeypatch: pytest.MonkeyPatch, failure: Exception | None) -> list[str]:
    sent: list[str] = []

    class _Handle:
        def __init__(self, workflow_id: str) -> None:
            self._workflow_id = workflow_id

        async def signal(self, _signal) -> None:
            if failure is not None:
                raise failure
            sent.append(self._workflow_id)

    class _Client:
        def get_workflow_handle(self, workflow_id: str) -> _Handle:
            return _Handle(workflow_id)

    async def fake_connect():
        return _Client()

    monkeypatch.setattr("app.routers.jobs.connect", fake_connect)
    return sent


@pytest.mark.parametrize("action", ["approve", "reject"])
def test_signal_reaches_the_workflow(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    action: str,
) -> None:
    sent = _signal_client(monkeypatch, None)
    response = client.post(f"/jobs/job-1/{action}")
    assert response.status_code == 200
    assert response.json() == {"id": "job-1", "signal": action}
    assert sent == ["job-1"]


@pytest.mark.parametrize("action", ["approve", "reject"])
def test_signal_for_unknown_job_is_not_found(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    action: str,
) -> None:
    _signal_client(monkeypatch, RPCError("workflow not found", RPCStatusCode.NOT_FOUND, b""))
    response = client.post(f"/jobs/missing/{action}")
    assert response.status_code == 404


def test_signal_other_rpc_error_is_not_hidden(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _signal_client(monkeypatch, RPCError("down", RPCStatusCode.UNAVAILABLE, b""))
    with pytest.raises(RPCError):
        client.post("/jobs/job-1/approve")


def _skip_storage(monkeypatch: pytest.MonkeyPatch) -> None:
    async def missing(*_args, **_kwargs):
        return None

    async def inserted(document_id, *_args, **_kwargs):
        return document_id, True

    class _Client:
        async def start_workflow(self, *_args, **_kwargs) -> None:
            return None

    async def fake_connect():
        return _Client()

    monkeypatch.setattr("app.routers.documents.find_document_by_hash", missing)
    monkeypatch.setattr("app.routers.documents.insert_document", inserted)
    monkeypatch.setattr("app.routers.documents.connect", fake_connect)
