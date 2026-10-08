import re
from dataclasses import dataclass

from app.core.config import settings
from app.core.logging import get_logger
from app.repositories.search import FragmentHit
from app.schemas.cards import CardDraft, SecurityFinding, SecurityReport
from app.services import injection as injection_service
from app.services.context import BuiltContext, build_context
from app.services.pii import mask_pii


@dataclass
class ScreenedContext:
    built: BuiltContext
    security: SecurityReport
    blocked: bool = False
    block_reason: str | None = None


def screen_hits(hits: list[FragmentHit], limit: int) -> ScreenedContext:
    """Маскирование PII, затем отсев инъекций. До промпта и до логов контекста."""
    counters: dict[str, int] = {}
    masked_hits: list[FragmentHit] = []
    findings: list[SecurityFinding] = []
    excluded: list[SecurityFinding] = []
    suspicious = 0

    for hit in hits:
        masked = mask_pii(hit.text, counters)
        for item in masked.hits:
            findings.append(
                SecurityFinding(kind=item.kind, label=item.label, fragment_id=hit.id)
            )
            get_logger().info(
                "pii_masked",
                kind=item.kind,
                label=item.label,
                fragment_id=hit.id,
            )
        safe = FragmentHit(
            id=hit.id,
            document_id=hit.document_id,
            page=hit.page,
            section=hit.section,
            article=hit.article,
            brand=hit.brand,
            text=masked.text,
            score=hit.score,
        )
        is_bad, reason = injection_service.examine_fragment(safe.text)
        if is_bad:
            suspicious += 1
            excluded.append(
                SecurityFinding(kind="injection", label=reason, fragment_id=hit.id)
            )
            get_logger().info("injection_excluded", fragment_id=hit.id, reason=reason)
            continue
        masked_hits.append(safe)

    if settings.too_many_suspicious(suspicious):
        reason = (
            f"подозрительных фрагментов {suspicious}, "
            f"допустимо не больше {settings.suspicious_chunk_limit}"
        )
        report = SecurityReport(
            masked=findings,
            excluded=excluded,
            blocked=True,
            block_reason=reason,
        )
        empty = BuiltContext(fragments=[], text="", size=0)
        return ScreenedContext(
            built=empty,
            security=report,
            blocked=True,
            block_reason=reason,
        )

    built = build_context(masked_hits, limit)
    get_logger().info(
        "context_screened",
        size=built.size,
        fragments=len(built.fragments),
        masked=len(findings),
        excluded=len(excluded),
    )
    return ScreenedContext(
        built=built,
        security=SecurityReport(masked=findings, excluded=excluded),
    )


_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+|\n+")


def _drop_flagged_sentences(text: str) -> str:
    """Предложение со следом инструкции убирается целиком, а не вырезается по совпадению.

    Вырезание куска давало обрубки вроде «Всего ь!». Если след в единственном предложении
    (заголовок, значение характеристики), поле остаётся пустым: пустое лучше искажённого.
    """
    kept = [
        part
        for part in _SENTENCE_BREAK.split(text)
        if part.strip() and not injection_service.rule_scan(part)
    ]
    return " ".join(kept).strip()


def filter_card_output(draft: CardDraft) -> tuple[CardDraft, list[SecurityFinding]]:
    """Фильтр выхода: PII и следы служебных инструкций в карточке."""
    counters: dict[str, int] = {}
    findings: list[SecurityFinding] = []
    data = draft.model_dump()

    def _clean(value: str) -> str:
        masked = mask_pii(value, counters)
        for item in masked.hits:
            findings.append(
                SecurityFinding(kind=item.kind, label=item.label, fragment_id=None)
            )
        cleaned = masked.text
        hits = injection_service.rule_scan(cleaned)
        if hits:
            findings.append(
                SecurityFinding(
                    kind="injection",
                    label="следы инструкций на выходе: " + ",".join(item.rule for item in hits),
                    fragment_id=None,
                )
            )
            cleaned = _drop_flagged_sentences(cleaned)
        return cleaned

    data["title"] = _clean(str(data.get("title") or ""))
    data["description"] = _clean(str(data.get("description") or ""))
    benefits = [_clean(str(item)) for item in data.get("benefits") or []]
    data["benefits"] = [item for item in benefits if item]
    data["characteristics"] = {
        key: _clean(str(value)) for key, value in (data.get("characteristics") or {}).items()
    }
    seo = data.get("seo") or {}
    seo["title"] = _clean(str(seo.get("title") or ""))
    seo["description"] = _clean(str(seo.get("description") or ""))
    data["seo"] = seo
    return CardDraft.model_validate(data), findings
