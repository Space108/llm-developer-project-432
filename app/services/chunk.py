from app.core.config import settings
from app.parsers.models import FragmentDraft, TableRow, TextBlock


def chunk_text(text: str, size: int, overlap: int) -> list[str]:
    """Фиксированный размер с перекрытием. Пустой текст не даёт фрагментов."""
    body = text.strip()
    if not body:
        return []
    if size <= 0 or len(body) <= size:
        return [body]
    if overlap < 0:
        overlap = 0
    if overlap >= size:
        overlap = 0
    pieces: list[str] = []
    start = 0
    while start < len(body):
        end = min(start + size, len(body))
        pieces.append(body[start:end])
        if end >= len(body):
            break
        next_start = end - overlap
        if next_start <= start:
            next_start = end
        start = next_start
    return pieces


def chunk_blocks(
    blocks: list[TextBlock],
    size_or_doc: int | str,
    overlap: int | None = None,
) -> list[FragmentDraft] | list[dict]:
    """Резка блоков. Каркас Хекслета: chunk_blocks(blocks, doc_id)."""
    if isinstance(size_or_doc, str):
        doc_id = size_or_doc
        size = settings.chunk_size
        use_overlap = settings.chunk_overlap if overlap is None else overlap
        drafts = _chunk_to_drafts(blocks, size, use_overlap)
        return [
            {
                "chunk_id": f"{doc_id}:{index}",
                "text": item.text,
                "metadata": {
                    "doc_id": doc_id,
                    "page": item.page,
                    "section": item.section,
                    "kind": "text",
                },
            }
            for index, item in enumerate(drafts)
        ]
    size = size_or_doc
    if overlap is None:
        raise TypeError("chunk_blocks() missing 1 required positional argument: 'overlap'")
    return _chunk_to_drafts(blocks, size, overlap)


def _chunk_to_drafts(blocks: list[TextBlock], size: int, overlap: int) -> list[FragmentDraft]:
    fragments: list[FragmentDraft] = []
    for block in blocks:
        for piece in chunk_text(block.text, size, overlap):
            fragments.append(
                FragmentDraft(
                    page=block.page,
                    section=block.section,
                    article="",
                    brand="",
                    text=piece,
                )
            )
    return fragments


def rows_to_fragments(rows: list[TableRow]) -> list[FragmentDraft]:
    """Строка таблицы целиком. Заголовки колонок уже внутри текста строки."""
    return [
        FragmentDraft(
            page=row.page,
            section=row.section,
            article=row.article,
            brand=row.brand,
            text=row.text,
        )
        for row in rows
        if row.text.strip()
    ]
