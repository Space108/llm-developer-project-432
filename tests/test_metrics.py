from app.agents.prompts import judge_prompt
from app.repositories.search import FragmentHit
from app.schemas.cards import CardDraft, JudgeVerdict, SourceRef
from app.services import pipeline
from app.services.metrics import citation_match_rate, judge_card
from app.services.normalize_metrics import characteristics_match_rate, normalize_value


def test_normalize_treats_power_spellings_as_equal() -> None:
    assert normalize_value("800 Вт") == normalize_value("800 вт")
    assert normalize_value("800 Вт.") == normalize_value("800 Вт")


def test_characteristics_match_rate_ignores_case_and_dot() -> None:
    expected = {"Мощность": "800 Вт"}
    actual = {"мощность": "800 вт."}
    assert characteristics_match_rate(expected, actual) == 1.0


def test_citation_match_rate_needs_probe_text_in_cited_fragment() -> None:
    draft = CardDraft(
        title="Блендер",
        description="Мощность 800 Вт.",
        characteristics={"Мощность": "800 Вт"},
        benefits=["Турбо"],
        sources=[SourceRef(chunk_id="f1", page=1, section="Технические характеристики")],
    )
    fragments = [
        FragmentHit(
            id="f1",
            document_id="d1",
            page=1,
            section="Технические характеристики",
            article="",
            brand="",
            text="Мощность\n800 Вт\nГарантия\n24 месяца",
            score=1,
        )
    ]
    probes = {"Мощность": {"page": 1, "text_contains": "800 Вт"}}
    assert citation_match_rate(draft, fragments, probes) == 1.0
    bad = CardDraft(
        title="Блендер",
        description="x",
        characteristics={"Мощность": "800 Вт"},
        benefits=["Турбо"],
        sources=[SourceRef(chunk_id="f1", page=1)],
    )
    fragments[0].text = "совсем другой текст"
    assert citation_match_rate(bad, fragments, probes) == 0.0


def test_judge_prompt_asks_for_supported_verdict(monkeypatch) -> None:
    seen: dict = {}

    def complete(_self, system: str, user: str, **kwargs: object) -> str:
        seen["system"] = system
        seen["user"] = user
        seen["cheap"] = kwargs.get("cheap")
        return JudgeVerdict(supported=True).model_dump_json()

    monkeypatch.setattr(pipeline.LlmClient, "complete", complete)
    draft = CardDraft(
        title="Блендер",
        description="Мощность 800 Вт.",
        characteristics={"Мощность": "800 Вт"},
        benefits=["Турбо"],
        sources=[SourceRef(chunk_id="f1")],
    )
    fragments = [
        FragmentHit(
            id="f1",
            document_id="d1",
            page=1,
            section="",
            article="",
            brand="",
            text="800 Вт",
            score=1,
        )
    ]
    verdict = judge_card(draft, fragments)
    assert verdict.supported is True
    assert "supported" in judge_prompt()
    assert "Карточка:" in seen["user"]
    assert "chunk_id: f1" in seen["user"]
    assert seen["cheap"] is True
