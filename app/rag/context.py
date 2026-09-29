"""Сборка контекста для генерации. Имя модуля — из каркаса Хекслета."""

from app.services.context import BuiltContext, build_context, format_source


def context_chunk_ids(built: BuiltContext) -> list[str]:
    """Идентификаторы фрагментов, попавших в контекст."""
    return [item.id for item in built.fragments]


__all__ = ["BuiltContext", "build_context", "context_chunk_ids", "format_source"]
