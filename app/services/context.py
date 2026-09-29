from dataclasses import dataclass

from app.repositories.search import FragmentHit


@dataclass
class BuiltContext:
    fragments: list[FragmentHit]
    text: str
    size: int


def format_source(hit: FragmentHit) -> str:
    """Фрагмент опознаваем по chunk_id, странице и разделу."""
    return (
        "[источник]\n"
        f"chunk_id: {hit.id}\n"
        f"page: {hit.page}\n"
        f"section: {hit.section}\n"
        f"text:\n{hit.text}"
    )


def build_context(hits: list[FragmentHit], limit: int) -> BuiltContext:
    """Одинаковый текст оставляем один раз. Размер — длина собранного контекста."""
    seen: set[str] = set()
    blocks: list[str] = []
    chosen: list[FragmentHit] = []
    for hit in hits:
        if hit.text in seen:
            continue
        block = format_source(hit)
        size = len("\n\n".join([*blocks, block]))
        if size > limit:
            break
        seen.add(hit.text)
        blocks.append(block)
        chosen.append(hit)
    text = "\n\n".join(blocks)
    return BuiltContext(fragments=chosen, text=text, size=len(text))
