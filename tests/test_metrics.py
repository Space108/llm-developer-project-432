import pytest
from app.agents.prompts import judge_prompt
from app.repositories.search import FragmentHit
from app.schemas.cards import CardDraft, JudgeVerdict, SourceRef
from app.services import metrics as metrics_service
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


def test_empty_expectations_measure_nothing() -> None:
    """Пустой эталон — `None`, а не 1.0: из единицы «за пустоту» не должно расти среднее."""
    assert characteristics_match_rate({}, {"Мощность": "800 Вт"}) is None
    assert citation_match_rate(_card("f1"), [_fragment("f1", "текст")], {}) is None


KP_ROWS = {
    "BLD-500": "МиксерПро 500, 500 Вт, белый, 1490 ₽",
    "BLD-800": "МиксерПро 800, 800 Вт, графитовый, 2190 ₽",
}


def test_article_row_is_compared_by_article_and_parameters() -> None:
    """Позиция «артикул → строка»: карточка не обязана повторять строку целиком."""
    actual = {"BLD-500": "500 Вт, белый, 1 490 ₽", "BLD-800": "800 Вт, графитовый, 2 190 ₽"}
    text = "Линейка МиксерПро 500 и МиксерПро 800"
    assert characteristics_match_rate(KP_ROWS, actual, extra_text=text) == 1.0


def test_article_row_gives_partial_credit_per_missing_parameter() -> None:
    # Артикул + 4 параметра строки = 5 признаков; нет цены → 4/5.
    row = {"BLD-800": "МиксерПро 800, 800 Вт, графитовый, 2190 ₽"}
    actual = {"BLD-800": "800 Вт, графитовый"}
    rate = characteristics_match_rate(row, actual, extra_text="МиксерПро 800")
    assert rate == pytest.approx(0.8)


def test_article_row_scores_parameters_even_without_article_code() -> None:
    """Карточка пишет «Мощность / цвет / цена», без кода BLD-800 — позицию всё равно считают."""
    row = {"BLD-800": "МиксерПро 800, 800 Вт, графитовый, 2190 ₽"}
    actual = {"Мощность": "800 Вт", "Цвет": "графитовый", "Цена": "2190 ₽"}
    # 4 параметра из 5 признаков (артикул отсутствует).
    assert characteristics_match_rate(row, actual, extra_text="МиксерПро 800") == pytest.approx(0.8)


def test_typical_marketplace_card_for_one_kp_position() -> None:
    """Обычная карточка по одной позиции КП: пары параметр→значение, без ключей-артикулов."""
    expected = {"BLD-800": "МиксерПро 800, 800 Вт, графитовый, 2190 ₽"}
    actual = {
        "Модель": "МиксерПро 800",
        "Мощность": "800 Вт",
        "Цвет": "графитовый",
        "Цена": "2 190 ₽",
    }
    assert characteristics_match_rate(expected, actual) == pytest.approx(0.8)


def test_decimal_comma_inside_a_row_is_not_a_separator() -> None:
    row = {"KTL-1700": "КеттлПро 1.7, 2200 Вт, 1,7 л, сталь/пластик, 0,9 кг"}
    actual = {"KTL-1700": "2200 Вт, 1.7 л, сталь/пластик, 0.9 кг"}
    assert characteristics_match_rate(row, actual, extra_text="КеттлПро 1.7") == 1.0


def test_plain_pairs_stay_exact() -> None:
    expected = {"Мощность": "800 Вт", "Цвет": "графитовый"}
    actual = {"Мощность": "800 Вт", "Цвет": "белый"}
    assert characteristics_match_rate(expected, actual) == 0.5


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


def _fragment(fragment_id: str, text: str, page: int = 1) -> FragmentHit:
    return FragmentHit(
        id=fragment_id,
        document_id="d1",
        page=page,
        section="",
        article="",
        brand="",
        text=text,
        score=1,
    )


def _card(*chunk_ids: str) -> CardDraft:
    return CardDraft(
        title="Блендер",
        description="Описание",
        sources=[SourceRef(chunk_id=item) for item in chunk_ids],
    )


def test_score_card_without_expectations_measures_nothing(monkeypatch) -> None:
    """Скан: эталон пуст, фрагментов нет. Единицы «за пустоту» быть не должно."""

    def judge_must_not_run(*_args: object, **_kwargs: object) -> JudgeVerdict:
        raise AssertionError("судью по пустой карточке не зовут")

    monkeypatch.setattr(metrics_service, "judge_card", judge_must_not_run)
    scores = metrics_service.score_card(_card(), [], {"characteristics": {}})
    assert scores["characteristics"] is None
    assert scores["citation"] is None
    assert scores["judge_supported"] is None
    assert scores["cited_fragment_ids"] == []


def test_score_card_measures_what_has_expectations(monkeypatch) -> None:
    monkeypatch.setattr(
        metrics_service, "judge_card", lambda *_args, **_kwargs: JudgeVerdict(supported=True)
    )
    fragments = [_fragment("f1", "Мощность 800 Вт")]
    expected = {
        "characteristics": {"Мощность": "800 Вт"},
        "source_probes": {"Мощность": {"page": 1, "text_contains": "800 Вт"}},
    }
    card = CardDraft(
        title="Блендер",
        description="Описание",
        characteristics={"Мощность": "800 Вт"},
        sources=[SourceRef(chunk_id="f1")],
    )
    scores = metrics_service.score_card(card, fragments, expected)
    assert scores["characteristics"] == 1.0
    assert scores["citation"] == 1.0
    assert scores["judge_supported"] is True
    assert scores["cited_fragment_ids"] == ["f1"]


def test_probe_diagnostics_tells_a_search_miss_from_a_wrong_link() -> None:
    probes = {
        "found_and_cited": {"page": None, "text_contains": "2 190"},
        "found_not_cited": {"page": None, "text_contains": "1 490"},
        "not_in_context": {"page": None, "text_contains": "3 450"},
    }
    fragments = [
        _fragment("price-table", "BLD-800 | 2 190 ₽\nBLD-500 | 1 490 ₽"),
        _fragment("intro", "Оптовые поставки блендеров"),
    ]
    result = metrics_service.probe_diagnostics(_card("price-table"), fragments, probes)
    assert result["found_and_cited"] == {"in_context": True, "cited": True}
    assert result["not_in_context"] == {"in_context": False, "cited": False}
    wrong = metrics_service.probe_diagnostics(_card("intro"), fragments, probes)
    assert wrong["found_and_cited"] == {"in_context": True, "cited": False}


def test_average_skips_metrics_that_measured_nothing() -> None:
    from app.commands.metrics import _avg, _counted

    assert _avg([1.0, 0.0, None, 1.0]) == pytest.approx(2 / 3)
    assert _avg([None, None]) is None
    assert _avg([]) is None
    assert _counted([1.0, None, 0.0]) == 2


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
