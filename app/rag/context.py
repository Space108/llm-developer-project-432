"""Сборка контекста для генерации. Имя модуля — из каркаса Хекслета."""

import re

from app.services import context as source_context

BuiltContext = source_context.BuiltContext
format_source = source_context.format_source

_HEADER_ID = re.compile(r"^\[([^|\]]+)")


def build_context(chunks, limit: int | None = None, *, max_chars: int | None = None):
    """Словарь чанков — строка с шапкой. FragmentHit — прежний собранный контекст."""
    if chunks and isinstance(chunks[0], dict):
        cap = max_chars if max_chars is not None else limit
        return _format_chunk_dicts(chunks, 100_000 if cap is None else cap)
    from app.core.config import settings

    cap = limit if limit is not None else max_chars
    if cap is None:
        cap = settings.context_size_limit
    return source_context.build_context(chunks, cap)


def context_chunk_ids(built: BuiltContext | str) -> list[str] | set[str]:
    """Идентификаторы фрагментов, попавших в контекст."""
    if isinstance(built, str):
        found: set[str] = set()
        for line in built.splitlines():
            match = _HEADER_ID.match(line)
            if match:
                found.add(match.group(1).strip())
        return found
    return [item.id for item in built.fragments]


def _format_chunk_dicts(chunks: list[dict], max_chars: int) -> str:
    blocks: list[str] = []
    for chunk in chunks:
        meta = chunk.get("metadata") or {}
        parts = [str(chunk.get("chunk_id") or "")]
        if chunk.get("filename"):
            parts.append(str(chunk["filename"]))
        if meta.get("page") is not None:
            parts.append(f"стр. {meta['page']}")
        if meta.get("section"):
            parts.append(str(meta["section"]))
        block = "[" + " | ".join(parts) + "]\n" + str(chunk.get("text") or "")
        candidate = "\n\n".join([*blocks, block])
        if blocks and len(candidate) >= max_chars:
            break
        blocks.append(block)
    return "\n\n".join(blocks)


__all__ = ["BuiltContext", "build_context", "context_chunk_ids", "format_source"]
