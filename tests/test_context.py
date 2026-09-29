from app.agents.prompts import context_critic_prompt, context_generator_prompt
from app.repositories.search import FragmentHit
from app.schemas.cards import CardDraft, CritiqueReport, SourceRef
from app.services import pipeline
from app.services.context import build_context


def _hit(fragment_id: str, body: str, document_id: str = "doc") -> FragmentHit:
    return FragmentHit(
        id=fragment_id,
        document_id=document_id,
        page=1,
        section="Спецификация",
        article="",
        brand="",
        text=body,
        score=1,
    )


def _draft(chunk_id: str, confidence: float = 1) -> CardDraft:
    return CardDraft(
        title="Блендер 800 Вт",
        description="Мощность 800 Вт.",
        characteristics={"Мощность": "800 Вт"},
        benefits=["Металлическая ножка"],
        sources=[SourceRef(chunk_id=chunk_id, page=1, section="Спецификация")],
        missing_fields=["гарантия"],
        confidence=confidence,
    )


def test_duplicate_text_from_another_document_is_dropped() -> None:
    built = build_context(
        [_hit("a", "один и тот же", "doc-1"), _hit("b", "один и тот же", "doc-2")],
        limit=10000,
    )
    assert [item.id for item in built.fragments] == ["a"]
    assert built.size == len(built.text)
    assert "chunk_id: a" in built.text
    assert "chunk_id: b" not in built.text


def test_context_size_stops_the_next_fragment() -> None:
    first = _hit("a", "один")
    alone = build_context([first], limit=10000)
    built = build_context([first, _hit("b", "два")], limit=alone.size)
    assert [item.id for item in built.fragments] == ["a"]
    assert built.size == alone.size


def test_context_prompts_require_links_and_a_check_against_the_source() -> None:
    generator = context_generator_prompt()
    critic = context_critic_prompt()
    assert "контекста источников" in generator
    assert "chunk_id" in generator
    assert "Сверь каждое утверждение" in critic
    assert "Ссылки обязательны" in critic


def test_known_source_is_accepted(monkeypatch) -> None:
    def complete(_self, system: str, _user: str, **_kwargs: object) -> str:
        if "копирайтер" in system:
            return _draft("real").model_dump_json()
        return CritiqueReport(verdict="approve").model_dump_json()

    monkeypatch.setattr(pipeline.LlmClient, "complete", complete)
    draft, attempts, verdict = pipeline.run_context_pipeline("контекст", {"real"}, {"real"})
    assert verdict == "согласовано"
    assert attempts == 1
    assert draft.sources[0].chunk_id == "real"


def test_fake_fragment_is_sent_back_and_the_repeat_waits_for_a_human(monkeypatch) -> None:
    prompts: list[str] = []

    def complete(_self, system: str, user: str, **_kwargs: object) -> str:
        if "копирайтер" in system:
            prompts.append(user)
            return _draft("no-such-fragment").model_dump_json()
        raise AssertionError("проверка не должна идти при выдуманной ссылке")

    monkeypatch.setattr(pipeline.LlmClient, "complete", complete)
    _draft_card, attempts, verdict = pipeline.run_context_pipeline(
        "источник real",
        {"real"},
        {"real"},
    )
    assert verdict == "ожидание"
    assert attempts == 2
    assert len(prompts) == 2
    assert "фрагмент no-such-fragment не существует" in prompts[1]
    assert "фрагмент no-such-fragment не был в контексте" in prompts[1]


def test_existing_fragment_outside_the_context_is_a_fake_link() -> None:
    errors = pipeline.citation_errors(_draft("other"), {"seen"}, {"seen", "other"})
    assert errors == ["фрагмент other не был в контексте"]


def test_missing_link_is_an_error() -> None:
    draft = _draft("real")
    draft.sources = []
    assert pipeline.citation_errors(draft, {"real"}, {"real"}) == ["нет ссылки на источник"]


def test_empty_context_does_not_call_the_model(monkeypatch) -> None:
    def complete(_self, *_args, **_kwargs: object) -> str:
        raise AssertionError("пустой контекст модель не зовёт")

    monkeypatch.setattr(pipeline.LlmClient, "complete", complete)
    draft, attempts, verdict = pipeline.run_context_pipeline("", set(), set())
    assert attempts == 0
    assert verdict == "ожидание"
    assert draft.sources == []
    assert draft.confidence == 0
    assert "title" in draft.missing_fields


def test_incomplete_card_keeps_confidence_below_one(monkeypatch) -> None:
    monkeypatch.setattr(pipeline.settings, "confidence_threshold", 0.5)

    def complete(_self, system: str, _user: str, **_kwargs: object) -> str:
        if "копирайтер" in system:
            return _draft("real", confidence=0.2).model_dump_json()
        return CritiqueReport(verdict="approve").model_dump_json()

    monkeypatch.setattr(pipeline.LlmClient, "complete", complete)
    draft, _attempts, verdict = pipeline.run_context_pipeline("контекст", {"real"}, {"real"})
    assert "гарантия" in draft.missing_fields
    assert draft.confidence < 1
    assert verdict == "ожидание"
