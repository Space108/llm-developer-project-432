"""Резка блоков на фрагменты. Имя модуля — из каркаса Хекслета."""

from app.services.chunk import chunk_blocks, chunk_text, rows_to_fragments

__all__ = ["chunk_blocks", "chunk_text", "rows_to_fragments"]
