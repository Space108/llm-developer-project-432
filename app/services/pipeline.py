import json
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from app.agents.prompts import (
    context_critic_prompt,
    context_generator_prompt,
    critic_prompt,
    extractor_prompt,
    generator_prompt,
    repair_prompt,
)
from app.core.config import settings
from app.core.logging import get_logger
from app.llm.client import LlmClient
from app.llm.parse import ModelResponseError, parse_model_json
from app.schemas.cards import CardDraft, CritiqueReport, SupplierFacts

HEAL_ATTEMPTS = 2
T = TypeVar("T", bound=BaseModel)


def extract_sync(supplier_text: str) -> SupplierFacts:
    """Синхронный путь для Temporal и локальных тестов."""
    return read_model(extractor_prompt(), supplier_text, SupplierFacts)


async def extract(supplier_text: str) -> SupplierFacts:
    """Каркас Хекслета: extract зовёт run_agent."""
    raw = await run_agent("extractor", supplier_text)
    data = parse_model_json(raw)
    return SupplierFacts.model_validate(data)


def generate(facts: SupplierFacts, feedback: list[str] | None = None) -> CardDraft:
    prompt = "Факты о товаре:\n" + facts.model_dump_json(indent=2)
    if feedback:
        lines = "\n".join(f"- {item}" for item in feedback)
        prompt += "\n\nЗамечания критика, учти:\n" + lines
    return read_model(generator_prompt(), prompt, CardDraft)


def empty_context_card() -> CardDraft:
    """Контекст пуст: модель ничего не видела, фактов нет."""
    return CardDraft(
        title="",
        description="",
        missing_fields=["title", "description", "characteristics", "benefits", "seo"],
        confidence=0,
        sources=[],
    )


def generate_from_context(
    context_text: str,
    feedback: list[str] | None = None,
    context_ids: list[str] | None = None,
) -> CardDraft:
    prompt = "Контекст из источников:\n" + context_text
    if context_ids:
        prompt += "\n\nДопустимые chunk_id: " + ", ".join(context_ids)
    if feedback:
        lines = "\n".join(f"- {item}" for item in feedback)
        prompt += "\n\nЗамечания, учти:\n" + lines
    return read_model(context_generator_prompt(), prompt, CardDraft)


def critique_context(context_text: str, draft: CardDraft) -> CritiqueReport:
    prompt = (
        "Контекст из источников:\n"
        + context_text
        + "\n\nЧерновик карточки для проверки:\n"
        + draft.model_dump_json(indent=2)
    )
    return read_model(context_critic_prompt(), prompt, CritiqueReport, cheap=True)


def citation_errors(draft: CardDraft, context_ids: set[str], existing_ids: set[str]) -> list[str]:
    """Ссылка годится, только если фрагмент есть в базе и он был в контексте."""
    errors: list[str] = []
    if not draft.sources:
        errors.append("нет ссылки на источник")
        return errors
    for source in draft.sources:
        chunk_id = source.chunk_id
        if chunk_id not in existing_ids:
            errors.append(f"фрагмент {chunk_id} не существует")
        if chunk_id not in context_ids:
            errors.append(f"фрагмент {chunk_id} не был в контексте")
    return errors


def run_context_pipeline(
    context_text: str,
    context_ids: set[str],
    existing_ids: set[str],
    max_attempts: int = 3,
) -> tuple[CardDraft, int, str]:
    """Генерация из контекста. Выдуманная ссылка — переделка, повтор — к человеку."""
    if not context_ids:
        return empty_context_card(), 0, "ожидание"
    feedback: list[str] | None = None
    draft: CardDraft | None = None
    verdict = "rejected"
    attempts = 0
    citation_failures = 0
    allowed = sorted(context_ids)
    for _ in range(max_attempts):
        attempts += 1
        draft = generate_from_context(context_text, feedback, allowed)
        errors = citation_errors(draft, context_ids, existing_ids)
        if errors:
            citation_failures += 1
            if citation_failures >= 2:
                verdict = "ожидание"
                break
            feedback = errors
            continue
        report = critique_context(context_text, draft)
        if report.verdict == "approve":
            verdict = "согласовано"
            break
        feedback = report.issues
    if draft is None:
        raise RuntimeError("pipeline produced no draft")
    if verdict == "согласовано" and draft.confidence < settings.confidence_threshold:
        verdict = "ожидание"
    return draft, attempts, verdict


def critique(facts: SupplierFacts, draft: CardDraft) -> CritiqueReport:
    prompt = (
        "Факты о товаре:\n"
        + facts.model_dump_json(indent=2)
        + "\n\nЧерновик карточки для проверки:\n"
        + draft.model_dump_json(indent=2)
    )
    return read_model(critic_prompt(), prompt, CritiqueReport, cheap=True)


async def run_pipeline(
    supplier_text: str,
    max_attempts: int = 3,
) -> tuple[CardDraft, int, str, str]:
    """Извлечение и круги генерации. Каркас Хекслета: async + 4 значения."""
    from app.services import structured as structured_mod

    facts = await extract(supplier_text)
    feedback: list[str] | None = None
    draft: CardDraft | None = None
    verdict = "rejected"
    status = "rejected"
    attempts = 0
    for _ in range(max_attempts):
        attempts += 1
        prompt = "Факты о товаре:\n" + facts.model_dump_json(indent=2)
        if feedback:
            lines = "\n".join(f"- {item}" for item in feedback)
            prompt += "\n\nЗамечания критика, учти:\n" + lines
        draft = await structured_mod.validate_or_retry(prompt)
        critique_prompt = (
            "Факты о товаре:\n"
            + facts.model_dump_json(indent=2)
            + "\n\nЧерновик карточки для проверки:\n"
            + draft.model_dump_json(indent=2)
        )
        raw = await run_agent("critique", critique_prompt)
        report = CritiqueReport.model_validate(parse_model_json(raw))
        if report.verdict == "approve":
            verdict = "approved"
            status = "approved"
            break
        feedback = report.issues
    if draft is None:
        raise RuntimeError("pipeline produced no draft")
    if verdict == "approved" and draft.confidence < settings.confidence_threshold:
        verdict = "awaiting_confirmation"
        status = "awaiting_confirmation"
    return draft, attempts, verdict, status


def run_pipeline_sync(supplier_text: str, max_attempts: int = 3) -> tuple[CardDraft, int, str]:
    """Синхронный путь для API и старых тестов."""
    facts = extract_sync(supplier_text)
    feedback: list[str] | None = None
    draft: CardDraft | None = None
    verdict = "rejected"
    attempts = 0
    for _ in range(max_attempts):
        attempts += 1
        draft = generate(facts, feedback)
        report = critique(facts, draft)
        if report.verdict == "approve":
            verdict = "approved"
            break
        feedback = report.issues
    if draft is None:
        raise RuntimeError("pipeline produced no draft")
    if verdict == "approved" and draft.confidence < settings.confidence_threshold:
        verdict = "awaiting_confirmation"
    return draft, attempts, verdict


def read_model(system: str, user: str, model: type[T], *, cheap: bool = False) -> T:
    schema = model.model_json_schema()
    instruction = _with_schema(system, schema)
    prompt = user
    caught: Exception | None = None
    for _attempt in range(HEAL_ATTEMPTS):
        raw = LlmClient().complete(instruction, prompt, schema=schema, cheap=cheap)
        get_logger().info("model_call", operation="complete", model=model.__name__)
        if raw is None or not str(raw).strip():
            raise ModelResponseError("пустой ответ")
        try:
            data = parse_model_json(raw)
        except ModelResponseError as exc:
            caught = exc
            prompt = _with_error(user, raw, exc)
            continue
        try:
            return model.model_validate(data)
        except ValidationError as exc:
            field = _one_field(exc)
            if isinstance(data, dict) and field is not None:
                return repair_field(data, field, model, schema)
            caught = exc
            prompt = _with_error(user, raw, exc)
    if caught is None:
        raise ModelResponseError("пустой ответ")
    raise ModelResponseError(str(caught)) from caught


async def run_agent(agent, prompt: str) -> str:
    """Точка вызова модели для тестов Хекслета."""
    return LlmClient().complete(str(agent), prompt)


def repair_field(data: dict, field: str, model: type[T], schema: dict) -> T:
    """Одно поле. Остальной черновик, включая описание, остаётся как был."""
    get_logger().info("model_call", operation="repair_field", field=field)
    if field == "title":
        error = "поле с заголовком длиннее допустимого, сократи"
    else:
        error = "исправь поле " + field
    field_schema = _field_schema(schema, field)
    user = (
        "Текущий черновик:\n"
        + json.dumps(data, ensure_ascii=False)
        + f"\n\nИсправь только поле {field}. {error}"
    )
    raw = LlmClient().complete(
        _with_schema(repair_prompt(field), field_schema),
        user,
        schema=field_schema,
    )
    if raw is None or not str(raw).strip():
        raise ModelResponseError("пустой ответ")
    fixed = parse_model_json(raw)
    if not isinstance(fixed, dict) or field not in fixed:
        raise ModelResponseError("в ответе нет поля " + field)
    updated = dict(data)
    updated[field] = fixed[field]
    try:
        return model.model_validate(updated)
    except ValidationError as exc:
        raise ModelResponseError(str(exc)) from exc


def _with_schema(system: str, schema: dict) -> str:
    return system + "\n\nСхема результата:\n" + json.dumps(schema, ensure_ascii=False)


def _with_error(user: str, raw: str, exc: Exception) -> str:
    return f"{user}\n\nОтвет:\n{raw}\n\nОшибка:\n{exc}"


def _field_schema(schema: dict, field: str) -> dict:
    properties = schema.get("properties") or {}
    return {
        "title": "FieldRepair",
        "type": "object",
        "properties": {field: properties.get(field, {"type": "string"})},
        "required": [field],
    }


def _one_field(exc: ValidationError) -> str | None:
    names: list[str] = []
    for err in exc.errors():
        loc = err.get("loc") or ()
        if not loc or not isinstance(loc[0], str):
            return None
        names.append(loc[0])
    if len(set(names)) != 1:
        return None
    return names[0]
