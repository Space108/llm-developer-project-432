"""Контракт карточки. Путь каркаса Хекслета."""

import json

from pydantic import ValidationError

from app.agents.prompts import repair_request
from app.llm.parse import parse_model_json
from app.schemas.cards import (
    CardDraft,
    CardRules,
    CritiqueReport,
    Seo,
    SeoBlock,
    SourceRef,
    SupplierFacts,
)


class CardValidationError(ValueError):
    """Карточка не прошла схему после всех попыток."""


async def run_agent(agent, prompt: str) -> str:
    """Точка вызова модели для тестов Хекслета."""
    from app.llm.client import run_agent as llm_run_agent

    return await llm_run_agent(agent, prompt)


async def validate_or_retry(prompt: str, *, max_attempts: int = 3) -> CardDraft:
    """Генерация карточки с повтором при ошибке схемы."""
    feedback = ""
    last_error: Exception | None = None
    for _ in range(max_attempts):
        user = prompt if not feedback else f"{prompt}\n\nОшибка:\n{feedback}"
        raw = await run_agent("generator", user)
        try:
            data = parse_model_json(raw) if isinstance(raw, str) else raw
            if isinstance(data, CardDraft):
                return data
            if isinstance(data, str):
                data = parse_model_json(data)
            return CardDraft.model_validate(data)
        except (ValidationError, ValueError, TypeError, json.JSONDecodeError) as exc:
            last_error = exc
            feedback = str(exc)
            continue
    raise CardValidationError(str(last_error) if last_error else "невалидная карточка")


async def regenerate_field(card: CardDraft, field: str, error: str) -> CardDraft:
    """Исправить одно поле карточки."""
    prompt = repair_request(card.model_dump_json(), field, error)
    raw = await run_agent("repair", prompt)
    data = parse_model_json(raw) if isinstance(raw, str) else raw
    if isinstance(data, str):
        data = parse_model_json(data)
    if not isinstance(data, dict) or field not in data:
        raise CardValidationError(f"в ответе нет поля {field}")
    updated = card.model_dump()
    updated[field] = data[field]
    return CardDraft.model_validate(updated)


def confidence_gate(card: CardDraft) -> str:
    """Порог уверенности. Имя из каркаса Хекслета."""
    from app.core.config import settings

    if card.confidence < settings.confidence_threshold:
        return "needs_review"
    return "done"


__all__ = [
    "CardDraft",
    "CardRules",
    "CardValidationError",
    "CritiqueReport",
    "Seo",
    "SeoBlock",
    "SourceRef",
    "SupplierFacts",
    "confidence_gate",
    "regenerate_field",
    "run_agent",
    "validate_or_retry",
]
