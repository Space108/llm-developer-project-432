from pathlib import Path

from app.services.ingest import NO_TEXT_LAYER, prepare_fragments

ROOT = Path(__file__).resolve().parents[1]
HEATER = (
    "heater_passport_htr_1000.pdf",
    "heater_passport_htr_1250.pdf",
    "heater_passport_htr_1500.pdf",
    "heater_passport_htr_1750.pdf",
    "heater_passport_htr_2000.pdf",
)
SPEC_ROWS = (
    "HTR-1000: Бренд ТеплоДар; Модель 1000; Мощность, Вт 1000; "
    "Площадь обогрева, м² 10; Вес, кг 4,5; Цена опт, ₽ 3 990",
    "HTR-1250: Бренд ТеплоДар; Модель 1250; Мощность, Вт 1250; "
    "Площадь обогрева, м² 12,5; Вес, кг 4,7; Цена опт, ₽ 4 440",
    "HTR-1500: Бренд ТеплоДар; Модель 1500; Мощность, Вт 1500; "
    "Площадь обогрева, м² 15; Вес, кг 4,8; Цена опт, ₽ 4 890",
)


def test_heater_passports_keep_article_and_page() -> None:
    for name in HEATER:
        outcome = prepare_fragments(ROOT / "data" / "bulk" / name)
        assert outcome.reason is None
        assert outcome.fragments
        stem = name.removeprefix("heater_passport_").removesuffix(".pdf")
        article = stem.upper().replace("_", "-")
        joined = "\n".join(item.text for item in outcome.fragments)
        assert article in joined
        assert outcome.fragments[0].page == 1
        assert all(item.section for item in outcome.fragments)


def test_heater_spec_rows_stay_whole() -> None:
    outcome = prepare_fragments(ROOT / "data" / "bulk" / "heater_spec_1.xlsx")
    assert outcome.reason is None
    assert [item.text for item in outcome.fragments] == list(SPEC_ROWS)
    assert [item.article for item in outcome.fragments] == ["HTR-1000", "HTR-1250", "HTR-1500"]
    assert {item.section for item in outcome.fragments} == {"Спецификация"}
    assert {item.page for item in outcome.fragments} == {1}


def test_blender_offer_uses_heading_as_section() -> None:
    outcome = prepare_fragments(ROOT / "data" / "blender_kp.docx")
    assert outcome.reason is None
    sections = {item.section for item in outcome.fragments}
    assert "Прайс-лист (опт)" in sections
    assert "Условия" in sections
    joined = "\n".join(item.text for item in outcome.fragments)
    assert "Смирнова Анна Викторовна" in joined


def test_boiler_scan_has_no_text_layer() -> None:
    outcome = prepare_fragments(ROOT / "data" / "boiler_scan.pdf")
    assert outcome.fragments == []
    assert outcome.reason == NO_TEXT_LAYER


def test_given_pdfs_fill_page_and_section() -> None:
    passport = prepare_fragments(ROOT / "data" / "blender_passport.pdf")
    assert passport.reason is None
    assert {item.section for item in passport.fragments} >= {
        "ПАСПОРТ ИЗДЕЛИЯ",
        "Технические характеристики",
        "Комплект поставки",
        "Правила эксплуатации",
        "Гарантийные обязательства",
    }
    supply = [item for item in passport.fragments if item.section == "Комплект поставки"]
    assert supply and {item.page for item in supply} == {2}
    passport_text = "\n".join(item.text for item in passport.fragments)
    assert "BLD-800" in passport_text
    assert "800 Вт" in passport_text

    manual = prepare_fragments(ROOT / "data" / "kettle_manual.pdf")
    assert manual.reason is None
    assert all(item.page == 1 and item.section for item in manual.fragments)
    assert "KTL-1700" in "\n".join(item.text for item in manual.fragments)


def test_kettle_spec_rows_stay_whole() -> None:
    outcome = prepare_fragments(ROOT / "data" / "kettle_spec.xlsx")
    assert outcome.reason is None
    assert len(outcome.fragments) == 3
    assert [item.article for item in outcome.fragments] == ["KTL-1700", "KTL-1000", "KTL-THRM"]
    assert "KTL-1000" not in outcome.fragments[0].text
    assert outcome.fragments[0].text.startswith("KTL-1700: ")
