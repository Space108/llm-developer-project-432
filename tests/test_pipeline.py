import json

import pytest
from app.core.config import settings
from app.llm.parse import ModelResponseError
from app.schemas.cards import CardDraft, CritiqueReport, SupplierFacts
from app.services import pipeline


def _facts() -> SupplierFacts:
    return SupplierFacts(
        product_name="Блендер погружной",
        characteristics={"Мощность": "800 Вт"},
    )


def _draft(title: str = "Блендер 800 Вт") -> CardDraft:
    return CardDraft(
        title=title,
        description="Погружной блендер с металлической ножкой.",
        characteristics={"Мощность": "800 Вт"},
        benefits=["Металлическая ножка"],
        confidence=1,
    )


def _patch(monkeypatch, script: list) -> None:
    calls = {"n": 0}

    def fake_complete(_system: str, _user: str) -> str:
        item = script[calls["n"]]
        calls["n"] += 1
        return item.model_dump_json()

    def complete(_self, system: str, user: str, **_kwargs: object) -> str:
        return fake_complete(system, user)

    monkeypatch.setattr(pipeline.LlmClient, "complete", complete)


def test_pipeline_approves_on_first_pass(monkeypatch) -> None:
    _patch(monkeypatch, [_facts(), _draft(), CritiqueReport(verdict="approve")])
    draft, attempts, verdict = pipeline.run_pipeline("текст")
    assert verdict == "approved"
    assert attempts == 1
    assert draft.title == "Блендер 800 Вт"


def test_pipeline_accepts_after_revision(monkeypatch) -> None:
    _patch(
        monkeypatch,
        [
            _facts(),
            _draft("Слишком длинный заголовок для карточки товара на витрине"),
            CritiqueReport(verdict="regenerate", issues=["заголовок длиннее 60"]),
            _draft("Блендер 800 Вт"),
            CritiqueReport(verdict="approve"),
        ],
    )
    draft, attempts, verdict = pipeline.run_pipeline("текст")
    assert verdict == "approved"
    assert attempts == 2
    assert draft.title == "Блендер 800 Вт"


def test_pipeline_stops_after_three_rounds(monkeypatch) -> None:
    script: list = [_facts()]
    for _ in range(3):
        script.append(_draft())
        script.append(CritiqueReport(verdict="regenerate", issues=["вода"]))
    _patch(monkeypatch, script)
    draft, attempts, verdict = pipeline.run_pipeline("текст", max_attempts=3)
    assert verdict == "rejected"
    assert attempts == 3
    assert draft.title == "Блендер 800 Вт"


def test_schema_text_goes_into_the_instruction(monkeypatch) -> None:
    seen: dict = {}

    def complete(_self, system: str, _user: str, **kwargs: object) -> str:
        seen["system"] = system
        seen["schema"] = kwargs.get("schema")
        return _draft().model_dump_json()

    monkeypatch.setattr(pipeline.LlmClient, "complete", complete)
    pipeline.generate(_facts())
    assert "Схема результата" in seen["system"]
    assert seen["schema"]["title"] == "CardDraft"


def test_invalid_json_is_sent_back_with_the_error(monkeypatch) -> None:
    prompts: list[str] = []

    def complete(_self, _system: str, user: str, **_kwargs: object) -> str:
        prompts.append(user)
        if len(prompts) == 1:
            return "это мусор"
        return _draft().model_dump_json()

    monkeypatch.setattr(pipeline.LlmClient, "complete", complete)
    draft = pipeline.generate(_facts())
    assert draft.title == "Блендер 800 Вт"
    assert len(prompts) == 2
    assert "не удалось разобрать ответ модели" in prompts[1]


def test_empty_response_is_an_error(monkeypatch) -> None:
    calls = {"n": 0}

    def complete(_self, _system: str, _user: str, **_kwargs: object) -> str:
        calls["n"] += 1
        return "   "

    monkeypatch.setattr(pipeline.LlmClient, "complete", complete)
    with pytest.raises(ModelResponseError, match="пустой ответ"):
        pipeline.generate(_facts())
    assert calls["n"] == 1


def test_long_title_is_repaired_without_rewriting_description(monkeypatch) -> None:
    description = "Погружной блендер с металлической ножкой."
    logs: list[dict] = []

    class _Logger:
        def info(self, event: str, **kwargs: object) -> None:
            logs.append({"event": event, **kwargs})

    def complete(_self, _system: str, user: str, **_kwargs: object) -> str:
        if "Текущий черновик" not in user:
            return json.dumps(
                {"title": "А" * 61, "description": description, "confidence": 1},
                ensure_ascii=False,
            )
        return json.dumps(
            {"title": "Блендер 800 Вт", "description": "совсем другой текст"},
            ensure_ascii=False,
        )

    monkeypatch.setattr(pipeline.LlmClient, "complete", complete)
    monkeypatch.setattr(pipeline, "get_logger", lambda: _Logger())
    draft = pipeline.generate(_facts())
    assert draft.title == "Блендер 800 Вт"
    assert draft.description == description
    assert any(
        item.get("operation") == "repair_field" and item.get("field") == "title" for item in logs
    )


def test_sparse_text_waits_for_a_human(monkeypatch) -> None:
    monkeypatch.setattr(pipeline.settings, "confidence_threshold", 0.5)
    facts = SupplierFacts(product_name="Товар", missing_fields=["description", "benefits"])
    draft = CardDraft(
        title="Товар",
        description="Коротко.",
        missing_fields=["description", "benefits"],
        confidence=0.2,
    )
    _patch(monkeypatch, [facts, draft, CritiqueReport(verdict="approve")])
    card, _attempts, verdict = pipeline.run_pipeline("мало данных")
    assert card.missing_fields
    assert card.confidence < settings.confidence_threshold
    assert verdict == "awaiting_confirmation"
    assert verdict != "готово"
