"""Сквозной RAG-сценарий. Путь каркаса Хекслета.

Идёт через те же модули, что и основной поток: маскирование и отсев инъекций из
`app.guardrails`, фильтр на выходе, одно правило блокировки (`Settings.too_many_suspicious`),
запросы к модели через `LlmClient` (см. `Runner.run`). ADR 0005.
"""

from app.agents.prompts import context_critic_prompt, context_generator_prompt
from app.core.config import settings
from app.core.db import connection, current_sync_connection
from app.llm.parse import parse_model_json
from app.rag.context import context_chunk_ids
from app.repositories.chunks import get_chunks_by_ids
from app.schemas.cards import CardDraft, CritiqueReport, SecurityReport
from app.services.retrieve import retrieve_context
from app.services.security import filter_card_output, screen_hits


class SourceValidationError(RuntimeError):
    """Ссылка на чанк, которого нет в контексте или в базе."""


class _Agent:
    """Описание агента для `Runner.run`: имя, системный промпт, схема ответа, модель."""

    def __init__(
        self,
        name: str,
        instructions: str = "",
        schema: dict | None = None,
        *,
        cheap: bool = False,
    ) -> None:
        self.name = name
        self.instructions = instructions
        self.schema = schema
        self.cheap = cheap


async def run_agent(agent, prompt: str) -> str:
    """Точка вызова модели. Тесты подменяют её на этом модуле."""
    from app.llm.client import run_agent as llm_run_agent

    return await llm_run_agent(agent, prompt)


async def retrieve_context_async(query: str, doc_ids: list[str] | None = None):
    """Поиск, защита и текст контекста. Тесты подменяют функцию целиком.

    Возвращает строку контекста (пустую, если нечего показывать модели) и отчёт защиты.
    """
    from app.guardrails import guard_context
    from app.rag.context import build_context
    from app.repositories.chunks import load_chunks, search_hybrid

    own = None
    conn = current_sync_connection()
    if conn is None:
        own = connection()
        conn = own.__enter__()
    try:
        # Порог тот же, что у основного поиска. Умолчание 0.0 у самих `search_*` — сигнатура
        # каркаса, сквозной сценарий его не использует.
        hits = search_hybrid(
            conn,
            query,
            top_k=8,
            threshold=settings.relevance_threshold,
            doc_ids=doc_ids,
        )
        chunks = load_chunks(conn, [hit["chunk_id"] for hit in hits])
    finally:
        if own is not None:
            own.__exit__(None, None, None)
    safe, report = await guard_context(chunks, settings.context_size_limit)
    return (build_context(safe) if safe else ""), report


def _stored_ids(conn, ids: list[str]) -> set[str]:
    """Какие из `ids` есть в базе. Без открытого соединения берём короткое своё."""
    if not ids:
        return set()
    if conn is not None:
        return set(get_chunks_by_ids(conn, ids))
    with connection() as own:
        return set(get_chunks_by_ids(own, ids))


def _missing_sources(draft: CardDraft, context: str, conn) -> list[str]:
    allowed = context_chunk_ids(context)
    if not isinstance(allowed, set):
        allowed = set(allowed)
    cited = [source.chunk_id for source in draft.sources]
    stored = _stored_ids(conn, cited)
    missing: list[str] = []
    for chunk_id in cited:
        if chunk_id not in allowed or chunk_id not in stored:
            missing.append(chunk_id)
    if not cited:
        missing.append("")
    return missing


def _request(query: str, context: str, feedback: str) -> str:
    prompt = f"Запрос: {query}\n\nКонтекст:\n{context}"
    if feedback:
        prompt += "\n\nЗамечания, учти:\n" + feedback
    return prompt


async def run_rag_pipeline(query: str, doc_ids: list[str] | None = None):
    """Генерация, проверка ссылок, критика. Низкая уверенность — needs_review."""
    from app.guardrails import guard_output

    context, guard = await retrieve_context_async(query, doc_ids)
    conn = current_sync_connection()
    generator = _Agent(
        "Generator", context_generator_prompt(), CardDraft.model_json_schema()
    )
    critic = _Agent(
        "Critic", context_critic_prompt(), CritiqueReport.model_json_schema(), cheap=True
    )
    feedback = ""
    last_error: SourceValidationError | None = None
    for attempt in range(1, 3):
        prompt = _request(query, context, feedback)
        raw = await run_agent(generator, prompt)
        draft = CardDraft.model_validate(parse_model_json(raw))
        missing = _missing_sources(draft, context, conn)
        if missing:
            feedback = "нет такого источника: " + ", ".join(item or "(пусто)" for item in missing)
            last_error = SourceValidationError(feedback)
            continue
        critique_raw = await run_agent(critic, prompt + "\n\n" + draft.model_dump_json())
        report = CritiqueReport.model_validate(parse_model_json(critique_raw))
        if report.verdict != "approve":
            feedback = "\n".join(report.issues) or "критик отклонил черновик"
            last_error = SourceValidationError(feedback)
            continue
        draft, output_report = guard_output(draft)
        if isinstance(guard, SecurityReport) and output_report.masked:
            guard = guard.model_copy(update={"masked": [*guard.masked, *output_report.masked]})
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
