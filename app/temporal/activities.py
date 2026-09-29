import asyncio
import json
from pathlib import Path

from temporalio import activity

from app.core.context import bind_ids
from app.core.logging import bind_log
from app.repositories.documents import (
    count_fragments,
    existing_fragment_ids,
    load_document,
    replace_fragments,
    update_document_status,
)
from app.repositories.jobs import update_job_status
from app.schemas.cards import CardDraft, SupplierFacts
from app.services import pipeline
from app.services.ingest import prepare_fragments


def _bind_trace(job_id: str | None = None, request_id: str | None = None) -> None:
    bind_ids(job_id=job_id or None, request_id=request_id or None)
    bind_log(job_id=job_id or None, request_id=request_id or None)


@activity.defn
async def extract_activity(
    supplier_text: str,
    job_id: str = "",
    request_id: str = "",
) -> str:
    _bind_trace(job_id, request_id)
    facts = await asyncio.to_thread(pipeline.extract_sync, supplier_text)
    return facts.model_dump_json()


@activity.defn
async def generate_activity(
    facts_json: str,
    feedback_json: str | None = None,
    job_id: str = "",
    request_id: str = "",
) -> str:
    _bind_trace(job_id, request_id)
    facts = SupplierFacts.model_validate_json(facts_json)
    feedback = json.loads(feedback_json) if feedback_json else None
    draft = await asyncio.to_thread(pipeline.generate, facts, feedback)
    return draft.model_dump_json()


@activity.defn
async def critique_activity(
    facts_json: str,
    draft_json: str,
    job_id: str = "",
    request_id: str = "",
) -> str:
    _bind_trace(job_id, request_id)
    facts = SupplierFacts.model_validate_json(facts_json)
    draft = CardDraft.model_validate_json(draft_json)
    report = await asyncio.to_thread(pipeline.critique, facts, draft)
    return report.model_dump_json()


@activity.defn
async def set_status_activity(payload_json: str) -> None:
    """Отдельный шаг: статус в базу, не из пайплайна."""
    payload = json.loads(payload_json)
    _bind_trace(payload.get("job_id"), payload.get("request_id"))
    await update_job_status(
        payload["job_id"],
        payload["status"],
        result_json=payload.get("result_json"),
        error=payload.get("error"),
        bump_attempts=bool(payload.get("bump_attempts")),
    )


@activity.defn
async def prepare_document_activity(document_id: str) -> None:
    """Разбор, если документ ещё не проиндексирован. Готовый индекс не перезаписывается."""
    row = await load_document(document_id)
    if row is None:
        return
    if row["status"] == "проиндексирован" and await count_fragments(document_id) > 0:
        return
    await parse_document_activity(document_id)


@activity.defn
async def search_context_activity(payload_json: str) -> str:
    """Гибридный поиск, маскирование и отсев инъекций. Модель эмбеддингов грузится внутри."""
    from app.services.retrieve import retrieve_context

    payload = json.loads(payload_json)
    _bind_trace(payload.get("job_id"), payload.get("request_id"))
    screened = await retrieve_context(payload["document_ids"], payload["product_hint"])
    return json.dumps(
        {
            "text": screened.built.text,
            "size": screened.built.size,
            "ids": [item.id for item in screened.built.fragments],
            "blocked": screened.blocked,
            "block_reason": screened.block_reason,
            "security": screened.security.model_dump(),
        },
        ensure_ascii=False,
    )


@activity.defn
async def filter_output_activity(
    draft_json: str,
    security_json: str = "{}",
    job_id: str = "",
    request_id: str = "",
) -> str:
    """Фильтр выхода: PII и следы инструкций в карточке."""
    from app.schemas.cards import SecurityReport
    from app.services.security import filter_card_output

    _bind_trace(job_id, request_id)
    draft = CardDraft.model_validate_json(draft_json)
    report = SecurityReport.model_validate_json(security_json)
    cleaned, findings = await asyncio.to_thread(filter_card_output, draft)
    report.masked.extend(findings)
    cleaned.security = report
    return cleaned.model_dump_json()


@activity.defn
async def empty_context_card_activity() -> str:
    return pipeline.empty_context_card().model_dump_json()


@activity.defn
async def generate_context_activity(
    context_text: str,
    feedback_json: str | None = None,
    context_ids_json: str | None = None,
    job_id: str = "",
    request_id: str = "",
) -> str:
    _bind_trace(job_id, request_id)
    feedback = json.loads(feedback_json) if feedback_json else None
    context_ids = json.loads(context_ids_json) if context_ids_json else None
    draft = await asyncio.to_thread(
        pipeline.generate_from_context,
        context_text,
        feedback,
        context_ids,
    )
    return draft.model_dump_json()


@activity.defn
async def verify_citations_activity(draft_json: str, context_ids_json: str) -> str:
    draft = CardDraft.model_validate_json(draft_json)
    context_ids = set(json.loads(context_ids_json))
    cited = [source.chunk_id for source in draft.sources]
    existing = await existing_fragment_ids(cited)
    errors = pipeline.citation_errors(draft, context_ids, existing)
    return json.dumps(errors, ensure_ascii=False)


@activity.defn
async def critique_context_activity(
    context_text: str,
    draft_json: str,
    job_id: str = "",
    request_id: str = "",
) -> str:
    _bind_trace(job_id, request_id)
    draft = CardDraft.model_validate_json(draft_json)
    report = await asyncio.to_thread(pipeline.critique_context, context_text, draft)
    return report.model_dump_json()


@activity.defn
async def parse_document_activity(document_id: str) -> None:
    """Разбор файла в потоке, затем фрагменты в базу. Пустой разбор — отказ с причиной."""
    row = await load_document(document_id)
    if row is None:
        return
    await update_document_status(document_id, "разбирается")
    try:
        outcome = await asyncio.to_thread(prepare_fragments, Path(row["path"]))
    except Exception as exc:
        await update_document_status(document_id, "отказ", error=str(exc))
        return
    if outcome.reason is not None:
        await update_document_status(document_id, "отказ", error=outcome.reason)
        return
    await replace_fragments(document_id, outcome.fragments)
    await update_document_status(document_id, "проиндексирован")


@activity.defn
async def index_document_activity(document_id: str) -> int:
    """Векторы фрагментов этого документа. Модель грузится внутри, не при импорте."""
    from app.services import index as index_service

    return await index_service.index_document(document_id)
