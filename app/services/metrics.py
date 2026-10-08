from app.agents.prompts import judge_prompt
from app.repositories.search import FragmentHit
from app.schemas.cards import CardDraft, JudgeVerdict
from app.services import pipeline
from app.services.normalize_metrics import characteristics_match_rate, normalize_value


def citation_match_rate(
    draft: CardDraft,
    fragments: list[FragmentHit],
    probes: dict[str, dict],
) -> float | None:
    """Доля проб эталона, где заявленный источник содержит ожидаемый текст.

    Проб нет — измерять нечего, результат `None`, а не 1.0.
    """
    if not probes:
        return None
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


def probe_diagnostics(
    draft: CardDraft,
    fragments: list[FragmentHit],
    probes: dict[str, dict],
) -> dict[str, dict[str, bool]]:
    """По каждой пробе: текст есть в найденном контексте и есть в процитированном источнике.

    `in_context=False` — нужный фрагмент не нашёл поиск (проблема контекста).
    `in_context=True, cited=False` — фрагмент был, но карточка сослалась на другой.
    """
    cited_ids = {source.chunk_id for source in draft.sources}
    result: dict[str, dict[str, bool]] = {}
    for key, probe in probes.items():
        needle = normalize_value(str(probe.get("text_contains") or ""))
        page = probe.get("page")
        in_context = False
        cited = False
        for fragment in fragments:
            if page is not None and fragment.page != page:
                continue
            if needle and needle in normalize_value(fragment.text):
                in_context = True
                cited = cited or fragment.id in cited_ids
        result[key] = {"in_context": in_context, "cited": cited}
    return result


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
    """Оценки одной карточки. Метрика без исходных данных даёт `None`, а не 1.0.

    Пустой эталон (скан без текста) и пустой контекст ничего не измеряют: единица от
    «нечего проверять» завышала бы среднее, а `judge` по пустой карточке не имеет смысла.
    """
    expected_characteristics = expected.get("characteristics") or {}
    probes = expected.get("source_probes") or {}
    card_text = " ".join([draft.title, draft.description, *draft.benefits])
    chars = characteristics_match_rate(
        expected_characteristics, draft.characteristics, extra_text=card_text
    )
    citation = citation_match_rate(draft, fragments, probes)
    verdict = judge_card(draft, fragments) if fragments else None
    return {
        "characteristics": chars,
        "citation": citation,
        "judge_supported": None if verdict is None else verdict.supported,
        "unsupported_claims": [] if verdict is None else verdict.unsupported_claims,
        "cited_fragment_ids": [source.chunk_id for source in draft.sources],
        "citation_probes": probe_diagnostics(draft, fragments, probes),
    }
