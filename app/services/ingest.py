from pathlib import Path
from typing import BinaryIO

from app.core.config import settings
from app.parsers.docx import parse_docx
from app.parsers.models import ParseOutcome
from app.parsers.pdf import parse_pdf
from app.parsers.xlsx import parse_xlsx
from app.services.chunk import chunk_blocks, rows_to_fragments
from app.services.normalize import normalize_blocks

NO_TEXT_LAYER = "нет текстового слоя"


def prepare_fragments(
    path: Path | str | bytes | BinaryIO,
    fmt: str | None = None,
    doc_id: str | None = None,
) -> ParseOutcome | list[dict]:
    """Разбор, нормализация и резка.

    Каркас Хекслета: parse_to_chunks(bytes, \"xlsx\", \"doc1\") → список чанков.
    """
    if fmt is not None:
        return _parse_to_hexlet_chunks(path, fmt, doc_id or "doc")

    suffix = _suffix(path)
    try:
        if suffix == ".pdf":
            blocks = parse_pdf(path)
            rows = []
        elif suffix == ".docx":
            blocks = parse_docx(path)
            rows = []
        elif suffix == ".xlsx":
            blocks = []
            rows = parse_xlsx(path)
        else:
            return ParseOutcome([], "неподдерживаемое расширение")
    except Exception as exc:
        return ParseOutcome([], str(exc))
    fragments = chunk_blocks(normalize_blocks(blocks), settings.chunk_size, settings.chunk_overlap)
    fragments.extend(rows_to_fragments(rows))
    fragments = [item for item in fragments if item.text.strip()]
    if not fragments:
        return ParseOutcome([], NO_TEXT_LAYER)
    return ParseOutcome(fragments)


def _parse_to_hexlet_chunks(
    path: Path | str | bytes | BinaryIO,
    fmt: str,
    doc_id: str,
) -> list[dict]:
    kind = fmt.lower().lstrip(".")
    if kind == "pdf":
        blocks = normalize_blocks(parse_pdf(path))
        return chunk_blocks(blocks, doc_id)
    if kind == "docx":
        blocks = normalize_blocks(parse_docx(path))
        return chunk_blocks(blocks, doc_id)
    if kind == "xlsx":
        rows = parse_xlsx(path)
        return [
            {
                "chunk_id": f"{doc_id}:{index}",
                "text": row.text,
                "metadata": {
                    "doc_id": doc_id,
                    "page": row.page,
                    "section": row.section,
                    "article": row.article,
                    "articul": row.article,
                    "brand": row.brand,
                    "kind": "table",
                },
            }
            for index, row in enumerate(rows)
            if row.text.strip()
        ]
    return []


def _suffix(path: Path | str | bytes | BinaryIO) -> str:
    if isinstance(path, bytes):
        return ""
    if isinstance(path, Path):
        return path.suffix.lower()
    if isinstance(path, str):
        return Path(path).suffix.lower()
    name = getattr(path, "name", "") or ""
    return Path(str(name)).suffix.lower()
