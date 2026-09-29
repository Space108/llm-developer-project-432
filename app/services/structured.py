"""Контракт карточки. Путь каркаса Хекслета."""

import json

from pydantic import ValidationError

from app.llm.client import LlmClient
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
    return LlmClient().complete(str(agent), prompt)


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


__all__ = [
    "CardDraft",
    "CardRules",
    "CardValidationError",
    "CritiqueReport",
    "Seo",
    "SeoBlock",
    "SourceRef",
    "SupplierFacts",
    "run_agent",
    "validate_or_retry",
]
