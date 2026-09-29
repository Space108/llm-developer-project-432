"""Контракт карточки. Путь каркаса Хекслета."""

from app.llm.client import LlmClient
from app.schemas.cards import (
    CardDraft,
    CardRules,
    CritiqueReport,
    Seo,
    SeoBlock,
    SourceRef,
    SupplierFacts,
)


async def run_agent(agent, prompt: str) -> str:
    """Точка вызова модели для тестов Хекслета."""
    return LlmClient().complete(str(agent), prompt)


__all__ = [
    "CardDraft",
    "CardRules",
    "CritiqueReport",
    "Seo",
    "SeoBlock",
    "SourceRef",
    "SupplierFacts",
    "run_agent",
]
