"""Защита контекста и выхода. Имена — из каркаса Хекслета."""

from app.core.config import settings
from app.repositories.search import FragmentHit
from app.schemas.cards import CardDraft, SecurityFinding, SecurityReport
from app.services import injection as injection_service
from app.services.pii import mask_pii
from app.services.security import ScreenedContext, filter_card_output, screen_hits

GuardReport = SecurityReport


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
    is_bad, reason = injection_service.examine_fragment(text)
    if is_bad:
        excluded.append(SecurityFinding(kind="injection", label=reason, fragment_id=None))
        return "", GuardReport(
            masked=findings,
            excluded=excluded,
            blocked=True,
            block_reason=reason,
        )
    return text, GuardReport(masked=findings, excluded=excluded)


def guard_output(draft: CardDraft) -> tuple[CardDraft, GuardReport]:
    """Фильтр карточки на выходе: PII и следы инструкций."""
    cleaned, findings = filter_card_output(draft)
    report = GuardReport(masked=findings)
    cleaned.security = report
    return cleaned, report


__all__ = ["GuardReport", "guard_context", "guard_output", "ScreenedContext"]
