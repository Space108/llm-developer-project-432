from pathlib import Path

from pypdf import PdfReader

from app.parsers.models import TextBlock


def parse_pdf(path: Path) -> list[TextBlock]:
    """Текст pdf постранично. Строка крупнее соседних задаёт секцию."""
    reader = PdfReader(str(path))
    blocks: list[TextBlock] = []
    for index, page in enumerate(reader.pages, start=1):
        blocks.extend(_page_blocks(index, page))
    return blocks


def _page_blocks(page_number: int, page) -> list[TextBlock]:
    lines = _page_lines(page)
    if not lines:
        return [TextBlock(page=page_number, section="", text=page.extract_text() or "")]
    return _section_blocks(page_number, lines)


def _page_lines(page) -> list[tuple[str, float]]:
    runs: list[tuple[str, float]] = []

    def visitor(text: str, _cm, _tm, _font, font_size) -> None:
        if text:
            runs.append((text, float(font_size or 0)))

    page.extract_text(visitor_text=visitor)
    return _lines(runs)


def _lines(runs: list[tuple[str, float]]) -> list[tuple[str, float]]:
    lines: list[tuple[str, float]] = []
    parts: list[str] = []
    size = 0.0
    for text, run_size in runs:
        pieces = text.split("\n")
        for index, piece in enumerate(pieces):
            if piece.strip():
                parts.append(piece)
                size = max(size, run_size)
            if index < len(pieces) - 1:
                _flush_line(lines, parts, size)
                parts = []
                size = 0.0
    _flush_line(lines, parts, size)
    return lines


def _flush_line(lines: list[tuple[str, float]], parts: list[str], size: float) -> None:
    text = "".join(parts).strip()
    if text:
        lines.append((text, size))


def _heading_indexes(lines: list[tuple[str, float]]) -> set[int]:
    found: set[int] = set()
    last = len(lines) - 1
    for index, (_text, size) in enumerate(lines):
        if size <= 0:
            continue
        neighbors: list[float] = []
        if index > 0:
            neighbors.append(lines[index - 1][1])
        if index < last:
            neighbors.append(lines[index + 1][1])
        if neighbors and all(size > item for item in neighbors):
            found.add(index)
    return found


def _section_blocks(page_number: int, lines: list[tuple[str, float]]) -> list[TextBlock]:
    heading_at = _heading_indexes(lines)
    names: list[str] = []
    chunks: list[list[str]] = []
    section = ""
    for index, (text, _size) in enumerate(lines):
        if index in heading_at:
            section = text
        if not chunks or names[-1] != section:
            names.append(section)
            chunks.append([text])
        else:
            chunks[-1].append(text)
    if len(names) > 1 and names[0] == "" and names[1]:
        chunks[1] = chunks[0] + chunks[1]
        del chunks[0]
        del names[0]
    return [
        TextBlock(page=page_number, section=name, text="\n".join(parts))
        for name, parts in zip(names, chunks, strict=True)
    ]
