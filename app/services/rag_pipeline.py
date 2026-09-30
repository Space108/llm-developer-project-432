"""Сквозной RAG-сценарий. Путь каркаса Хекслета."""

from app.core.config import settings
from app.core.db import connection, current_sync_connection
from app.llm.parse import parse_model_json
from app.rag.context import context_chunk_ids
from app.repositories.chunks import get_chunks_by_ids
from app.schemas.cards import CardDraft, CritiqueReport
from app.services.retrieve import retrieve_context
from app.services.security import filter_card_output, screen_hits


class SourceValidationError(RuntimeError):
    """Ссылка на чанк, которого нет в контексте или в базе."""


class _Agent:
    def __init__(self, name: str) -> None:
        self.name = name


async def run_agent(agent, prompt: str) -> str:
    """Точка вызова модели. Тесты подменяют её на этом модуле."""
    from app.llm.client import run_agent as llm_run_agent

    return await llm_run_agent(agent, prompt)


async def retrieve_context_async(query: str, doc_ids: list[str] | None = None):
    """Поиск и текст контекста. Тесты подменяют функцию целиком."""
    from app.guardrails import GuardReport
    from app.rag.context import build_context
    from app.repositories.chunks import search_hybrid

    own = None
    conn = current_sync_connection()
    if conn is None:
        own = connection()
        conn = own.__enter__()
    try:
        hits = search_hybrid(conn, query, top_k=8, doc_ids=doc_ids)
        known = get_chunks_by_ids(conn, [hit["chunk_id"] for hit in hits])
        chunks = [
            {
                "chunk_id": chunk_id,
                "text": "",
                "metadata": {"doc_id": (doc_ids or [""])[0]},
                "filename": "",
            }
            for chunk_id in known
        ]
        return build_context(chunks), GuardReport()
    finally:
        if own is not None:
            own.__exit__(None, None, None)


def _missing_sources(draft: CardDraft, context: str, conn) -> list[str]:
    allowed = context_chunk_ids(context)
    if not isinstance(allowed, set):
        allowed = set(allowed)
    cited = [source.chunk_id for source in draft.sources]
    stored = set(get_chunks_by_ids(conn, cited)) if conn is not None else set()
    missing: list[str] = []
    for chunk_id in cited:
        if chunk_id not in allowed or chunk_id not in stored:
            missing.append(chunk_id)
    if not cited:
        missing.append("")
    return missing


async def run_rag_pipeline(query: str, doc_ids: list[str] | None = None):
    """Генерация, проверка ссылок, критика. Низкая уверенность — needs_review."""
    context, guard = await retrieve_context_async(query, doc_ids)
    conn = current_sync_connection()
    feedback = ""
    last_error: SourceValidationError | None = None
    for attempt in range(1, 3):
        prompt = f"Запрос: {query}\n\nКонтекст:\n{context}"
        if feedback:
            prompt += "\n\nЗамечания, учти:\n" + feedback
        raw = await run_agent(_Agent("Generator"), prompt)
        draft = CardDraft.model_validate(parse_model_json(raw))
        missing = _missing_sources(draft, context, conn)
        if missing:
            feedback = "нет такого источника: " + ", ".join(item or "(пусто)" for item in missing)
            last_error = SourceValidationError(feedback)
            continue
        critique_raw = await run_agent(_Agent("Critic"), prompt + "\n\n" + draft.model_dump_json())
        report = CritiqueReport.model_validate(parse_model_json(critique_raw))
        if report.verdict != "approve":
            feedback = "\n".join(report.issues) or "критик отклонил черновик"
            last_error = SourceValidationError(feedback)
            continue
        status = "needs_review" if draft.confidence < settings.confidence_threshold else "done"
        return draft, attempt, "approved", status, guard
    raise last_error or SourceValidationError("источник не подтверждён")


__all__ = [
    "SourceValidationError",
    "filter_card_output",
    "retrieve_context",
    "retrieve_context_async",
    "run_agent",
    "run_rag_pipeline",
    "screen_hits",
]
