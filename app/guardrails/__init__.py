"""Защита контекста и выхода. Имена — из каркаса Хекслета."""

import asyncio
import json

from app.core.config import settings
from app.guardrails import injection as injection_mod
from app.repositories.search import FragmentHit
from app.schemas.cards import CardDraft, SecurityFinding, SecurityReport
from app.services.pii import mask_pii
from app.services.security import ScreenedContext, filter_card_output, screen_hits

GuardReport = SecurityReport


async def detect_injection_llm(text: str):
    """Реэкспорт с вызовом через модуль injection — патчи тестов Хекслета."""
    return await injection_mod.detect_injection_llm(text)


def guard_context(
    payload: str | list[FragmentHit],
    limit: int | None = None,
) -> tuple[str, GuardReport] | ScreenedContext:
    """Маскирование PII и отсев инъекций до модели.

    Строка — маска и проверка текста. Список фрагментов — полный screen_hits.
    """
    if isinstance(payload, list):
        return screen_hits(payload, limit if limit is not None else settings.context_size_limit)

    counters: dict[str, int] = {}
    masked = mask_pii(payload, counters)
    findings = [
        SecurityFinding(kind=item.kind, label=item.label, fragment_id=None)
        for item in masked.hits
    ]
    excluded: list[SecurityFinding] = []
    text = masked.text
    try:
        verdict = asyncio.run(detect_injection_llm(text))
    except Exception as exc:
        verdict = injection_mod.InjectionVerdict(
            suspicious=True,
            reason=f"детектор недоступен: {exc}",
        )
    if verdict.suspicious:
        reason = verdict.reason or "инъекция"
        excluded.append(SecurityFinding(kind="injection", label=reason, fragment_id=None))
        return "", GuardReport(
            masked=findings,
            excluded=excluded,
            blocked=True,
            block_reason=reason,
            suspicious_chunks=["context"],
        )
    return text, GuardReport(masked=findings, excluded=excluded)


def guard_output(draft: CardDraft | str) -> tuple[CardDraft | str, GuardReport]:
    """Фильтр карточки на выходе: PII и следы инструкций."""
    as_json = isinstance(draft, str)
    if as_json:
        data = json.loads(draft)
        card = CardDraft(
            title=str(data.get("title") or ""),
            description=str(data.get("description") or ""),
            characteristics=data.get("characteristics") or {},
            benefits=data.get("benefits") or [],
            seo=data.get("seo") or {},
            sources=data.get("sources") or [],
            missing_fields=data.get("missing_fields") or [],
            confidence=float(data.get("confidence") or 0),
        )
    else:
        card = draft
    cleaned, findings = filter_card_output(card)
    report = GuardReport(masked=findings)
    cleaned.security = report
    if as_json:
        return cleaned.model_dump_json(), report
    return cleaned, report


__all__ = [
    "GuardReport",
    "detect_injection_llm",
    "guard_context",
    "guard_output",
    "ScreenedContext",
]
