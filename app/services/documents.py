"""Документы: разбор и фрагменты. Путь каркаса Хекслета."""

from app.services.ingest import NO_TEXT_LAYER, prepare_fragments

parse_to_chunks = prepare_fragments

__all__ = ["NO_TEXT_LAYER", "parse_to_chunks", "prepare_fragments"]
