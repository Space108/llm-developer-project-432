"""Разбор JSON-ответа модели. Имя модуля — из каркаса Хекслета."""

from app.llm.parse import ModelResponseError, parse_model_json

LLMJsonError = ModelResponseError
parse_llm_json = parse_model_json

__all__ = ["LLMJsonError", "parse_llm_json", "ModelResponseError", "parse_model_json"]
