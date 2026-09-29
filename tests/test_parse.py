import pytest
from app.llm.parse import ModelResponseError, parse_model_json


def test_parse_clean_json() -> None:
    assert parse_model_json('{"title": "Блендер"}') == {"title": "Блендер"}


def test_parse_json_in_markdown_fence() -> None:
    text = '```json\n{"title": "Блендер"}\n```'
    assert parse_model_json(text) == {"title": "Блендер"}


def test_parse_json_with_text_around() -> None:
    text = 'Вот ответ:\n{"title": "Блендер"}\nНа этом всё.'
    assert parse_model_json(text) == {"title": "Блендер"}


def test_parse_garbage_is_an_error() -> None:
    with pytest.raises(ModelResponseError, match="не удалось разобрать"):
        parse_model_json("это мусор, не json")


def test_parse_empty_is_an_error() -> None:
    with pytest.raises(ModelResponseError, match="пустой ответ"):
        parse_model_json("   ")
