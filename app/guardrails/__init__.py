"""Защита контекста и выхода. Имена — из каркаса Хекслета."""

import json

from app.core.config import settings
from app.guardrails import injection as injection_mod
from app.repositories.search import FragmentHit
from app.schemas.cards import CardDraft, SecurityFinding, SecurityReport
from app.services.injection import hard_rule_names
from app.services.pii import mask_pii
from app.services.security import ScreenedContext, filter_card_output, screen_hits

GuardReport = SecurityReport


async def detect_injection_llm(text: str):
    """Реэкспорт с вызовом через модуль injection — патчи тестов Хекслета."""
    return await injection_mod.detect_injection_llm(text)


def _hard_rules_win(text: str, verdict):
    """Жёсткий маркер («SYSTEM:», «игнорируй инструкции») не отменяется ответом «чисто».

    Так же устроен основной поток (`examine_fragment`): слабая модель не открывает дверь.
    """
    if verdict.suspicious:
        return verdict
    hard = hard_rule_names(text)
    if not hard:
        return verdict
    return injection_mod.InjectionVerdict(suspicious=True, reason=",".join(hard))


async def guard_context(
    payload: str | list[FragmentHit] | list[dict],
    limit: int | None = None,
) -> tuple[str | list, GuardReport] | ScreenedContext:
    """Маскирование PII и отсев инъекций до модели."""
    if isinstance(payload, list) and payload and isinstance(payload[0], FragmentHit):
        return screen_hits(
            payload,
            limit if limit is not None else settings.context_size_limit,
        )

    if isinstance(payload, list):
        return await _guard_chunk_dicts(
            payload,
            limit if limit is not None else settings.context_size_limit,
        )

    counters: dict[str, int] = {}
    masked = mask_pii(payload, counters)
    findings = [
        SecurityFinding(kind=item.kind, label=item.label, fragment_id=None)
        for item in masked.hits
    ]
    excluded: list[SecurityFinding] = []
    text = masked.text
    try:
        verdict = await detect_injection_llm(text)
    except Exception as exc:
        verdict = injection_mod.InjectionVerdict(
            suspicious=True,
            reason=f"детектор недоступен: {exc}",
        )
    verdict = _hard_rules_win(text, verdict)
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


async def _guard_chunk_dicts(
    chunks: list[dict],
    limit: int,
) -> tuple[list[dict], GuardReport]:
    counters: dict[str, int] = {}
    findings: list[SecurityFinding] = []
    excluded: list[SecurityFinding] = []
    suspicious_ids: list[str] = []
    suspicious = 0
    safe: list[dict] = []
    size = 0

    for chunk in chunks:
        chunk_id = str(chunk.get("chunk_id") or "")
        raw = str(chunk.get("text") or "")
        masked = mask_pii(raw, counters)
        for item in masked.hits:
            findings.append(
                SecurityFinding(kind=item.kind, label=item.label, fragment_id=chunk_id or None)
            )
        try:
            regex = injection_mod.detect_injection_regex(masked.text)
            if regex.suspicious:
                try:
                    verdict = await detect_injection_llm(masked.text)
                except Exception as exc:
                    verdict = injection_mod.InjectionVerdict(
                        suspicious=True,
                        reason=f"детектор недоступен: {exc}",
                    )
                verdict = _hard_rules_win(masked.text, verdict)
            else:
                verdict = injection_mod.InjectionVerdict(suspicious=False)
        except Exception as exc:
            verdict = injection_mod.InjectionVerdict(
                suspicious=True,
                reason=f"детектор недоступен: {exc}",
            )
        if verdict.suspicious:
            suspicious += 1
            reason = verdict.reason or "инъекция"
            excluded.append(
                SecurityFinding(kind="injection", label=reason, fragment_id=chunk_id or None)
            )
            if chunk_id:
                suspicious_ids.append(chunk_id)
            continue
        piece = dict(chunk)
        piece["text"] = masked.text
        if size + len(masked.text) > limit and safe:
            break
        safe.append(piece)
        size += len(masked.text)

    blocked = settings.too_many_suspicious(suspicious)
    reason = None
    if blocked:
        reason = (
            f"подозрительных фрагментов {suspicious}, "
            f"допустимо не больше {settings.suspicious_chunk_limit}"
        )
        safe = []
    return safe, GuardReport(
        masked=findings,
        excluded=excluded,
        blocked=blocked,
        block_reason=reason,
        suspicious_chunks=suspicious_ids,
    )


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
