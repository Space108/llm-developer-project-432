import asyncio
import json
import uuid
from collections.abc import Callable

import pytest
from app.schemas.cards import CardDraft, SourceRef
from app.temporal.workflows import CardWorkflow
from temporalio import activity
from temporalio.client import Client
from temporalio.worker import Worker

CITATION_ERROR = "фрагмент fake не существует"
DRAFT = CardDraft(
    title="Блендер",
    description="Погружной блендер.",
    sources=[SourceRef(chunk_id="fake")],
    confidence=0.9,
).model_dump_json()


def _fake_activities(
    verify_errors: Callable[[int], list[str]],
    verdict: str,
) -> tuple[dict, list]:
    """Подставные шаги процесса: базы и модели нет, считаем вызовы и статусы."""
    state: dict = {"verify_calls": 0, "statuses": []}

    @activity.defn(name="prepare_document_activity")
    async def prepare(document_id: str) -> None:
        return None

    @activity.defn(name="index_document_activity")
    async def index(document_id: str) -> int:
        return 0

    @activity.defn(name="search_context_activity")
    async def search(payload: str) -> str:
        return json.dumps(
            {
                "text": "контекст",
                "size": 8,
                "ids": ["real"],
                "blocked": False,
                "block_reason": None,
                "security": {},
            },
            ensure_ascii=False,
        )

    @activity.defn(name="generate_context_activity")
    async def generate(text, feedback, ids, job_id, request_id) -> str:
        return DRAFT

    @activity.defn(name="verify_citations_activity")
    async def verify(draft: str, ids: str) -> str:
        state["verify_calls"] += 1
        return json.dumps(verify_errors(state["verify_calls"]), ensure_ascii=False)

    @activity.defn(name="critique_context_activity")
    async def critique(text, draft, job_id, request_id) -> str:
        return json.dumps({"verdict": verdict, "issues": ["замечание"]}, ensure_ascii=False)

    @activity.defn(name="filter_output_activity")
    async def filter_output(draft, security, job_id, request_id) -> str:
        return draft

    @activity.defn(name="empty_context_card_activity")
    async def empty() -> str:
        return DRAFT

    @activity.defn(name="set_status_activity")
    async def set_status(payload: str) -> None:
        state["statuses"].append(json.loads(payload))

    activities = [
        prepare,
        index,
        search,
        generate,
        verify,
        critique,
        filter_output,
        empty,
        set_status,
    ]
    return state, activities


async def _connect() -> Client:
    try:
        return await asyncio.wait_for(Client.connect("localhost:7233"), timeout=3)
    except Exception as exc:
        pytest.skip(f"temporal unavailable: {exc}")


async def _run_until_waiting(
    verify_errors: Callable[[int], list[str]],
    verdict: str,
) -> dict:
    client = await _connect()
    state, activities = _fake_activities(verify_errors, verdict)
    job_id = "cit" + uuid.uuid4().hex[:9]
    queue = f"card-citations-{job_id}"
    async with Worker(client, task_queue=queue, workflows=[CardWorkflow], activities=activities):
        handle = await client.start_workflow(
            CardWorkflow.run,
            args=[job_id, "", 3, ["doc"], "блендер", "req-test"],
            id=job_id,
            task_queue=queue,
        )
        for _ in range(200):
            if any(item["status"] == "ожидание" for item in state["statuses"]):
                break
            await asyncio.sleep(0.1)
        else:
            raise AssertionError(f"процесс не дошёл до ожидания: {state['statuses']}")
        await handle.signal(CardWorkflow.approve)
        await asyncio.wait_for(handle.result(), timeout=20)
    return state


def _waiting(state: dict) -> dict:
    return next(item for item in state["statuses"] if item["status"] == "ожидание")


async def test_clean_card_waits_for_human_without_error() -> None:
    state = await _run_until_waiting(lambda _call: [], "approve")
    assert state["verify_calls"] == 1
    assert "error" not in _waiting(state)
    assert state["statuses"][-1]["status"] == "согласовано"


async def test_citation_error_on_last_attempt_is_shown_to_human() -> None:
    # Критик дважды просит переделку, ссылка ломается только на третьей попытке:
    # вторая ошибка не случилась, но карточка с выдуманным источником не уходит молча.
    state = await _run_until_waiting(
        lambda call: [CITATION_ERROR] if call == 3 else [],
        "regenerate",
    )
    assert state["verify_calls"] == 3
    assert _waiting(state)["error"] == CITATION_ERROR


async def test_repeated_citation_error_goes_to_human() -> None:
    state = await _run_until_waiting(lambda _call: [CITATION_ERROR], "approve")
    assert state["verify_calls"] == 2
    assert _waiting(state)["error"] == CITATION_ERROR
