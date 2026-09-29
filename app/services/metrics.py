from app.agents.prompts import judge_prompt
from app.repositories.search import FragmentHit
from app.schemas.cards import CardDraft, JudgeVerdict
from app.services import pipeline
from app.services.normalize_metrics import characteristics_match_rate, normalize_value


def citation_match_rate(
    draft: CardDraft,
    fragments: list[FragmentHit],
    probes: dict[str, dict],
) -> float:
    """Доля проб эталона, где заявленный источник содержит ожидаемый текст."""
    if not probes:
        return 1.0
    by_id = {item.id: item for item in fragments}
    cited = [by_id[source.chunk_id] for source in draft.sources if source.chunk_id in by_id]
    hits = 0
    for key, probe in probes.items():
        needle = normalize_value(str(probe.get("text_contains") or ""))
        page = probe.get("page")
        found = False
        for fragment in cited:
            if page is not None and fragment.page != page:
                continue
            if needle and needle in normalize_value(fragment.text):
                found = True
                break
        if found:
            hits += 1
    return hits / len(probes)


def judge_card(draft: CardDraft, fragments: list[FragmentHit]) -> JudgeVerdict:
    blocks = []
    for item in fragments:
        blocks.append(f"chunk_id: {item.id}\npage: {item.page}\ntext:\n{item.text}")
    prompt = (
        "Карточка:\n"
        + draft.model_dump_json(indent=2)
        + "\n\nФрагменты:\n"
        + "\n\n".join(blocks)
    )
    return pipeline.read_model(judge_prompt(), prompt, JudgeVerdict, cheap=True)


def score_card(
    draft: CardDraft,
    fragments: list[FragmentHit],
    expected: dict,
) -> dict:
    chars = characteristics_match_rate(
        expected.get("characteristics") or {},
        draft.characteristics,
    )
    citation = citation_match_rate(draft, fragments, expected.get("source_probes") or {})
    verdict = judge_card(draft, fragments)
    return {
        "characteristics": chars,
        "citation": citation,
        "judge_supported": verdict.supported,
        "unsupported_claims": verdict.unsupported_claims,
    }
