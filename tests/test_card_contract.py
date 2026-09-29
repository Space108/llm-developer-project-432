import pytest
from app.schemas.cards import TITLE_MAX_LENGTH, ProductCard
from pydantic import ValidationError


def test_title_is_not_longer_than_the_limit() -> None:
    ProductCard(title="А" * TITLE_MAX_LENGTH)
    with pytest.raises(ValidationError):
        ProductCard(title="А" * (TITLE_MAX_LENGTH + 1))


def test_confidence_stays_between_zero_and_one() -> None:
    ProductCard(confidence=0)
    ProductCard(confidence=1)
    with pytest.raises(ValidationError):
        ProductCard(confidence=1.2)


def test_empty_characteristic_does_not_stay_on_the_card() -> None:
    card = ProductCard(characteristics={"Мощность": "800 Вт", "Цвет": "  "})
    assert card.characteristics == {"Мощность": "800 Вт"}


def test_field_cannot_be_in_both_lists() -> None:
    with pytest.raises(ValidationError, match="поле в двух списках"):
        ProductCard(characteristics={"Мощность": "800 Вт"}, missing_fields=["Мощность"])


def test_seo_block_is_nested() -> None:
    card = ProductCard(seo={"title": "Блендер", "description": "Коротко"})
    assert card.seo.title == "Блендер"
    assert card.seo.description == "Коротко"
