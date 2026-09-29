import re

from app.parsers.models import TextBlock

_LIGATURES = str.maketrans(
    {
        "\ufb00": "ff",
        "\ufb01": "fi",
        "\ufb02": "fl",
        "\ufb03": "ffi",
        "\ufb04": "ffl",
    }
)


def normalize_blocks(blocks: list[TextBlock]) -> list[TextBlock]:
    """Колонтитулы, переносы через дефис, лигатуры и лишние пробелы."""
    cleaned = [_clean_block(block) for block in blocks]
    headers = _repeating_lines(cleaned)
    result: list[TextBlock] = []
    for block in cleaned:
        lines = [line for line in block.text.splitlines() if line not in headers]
        text = _join_hyphens("\n".join(lines))
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r" *\n *", "\n", text).strip()
        result.append(TextBlock(page=block.page, section=block.section, text=text))
    return result


def _clean_block(block: TextBlock) -> TextBlock:
    lines = [_clean_line(line) for line in block.text.splitlines()]
    lines = [line for line in lines if line]
    return TextBlock(page=block.page, section=block.section, text="\n".join(lines))


def _clean_line(line: str) -> str:
    line = line.translate(_LIGATURES).replace("\u00a0", " ")
    return re.sub(r"[ \t]+", " ", line).strip()


def _repeating_lines(blocks: list[TextBlock]) -> set[str]:
    """Строка — колонтитул, только если она есть строго больше чем на половине страниц.

    На двух страницах половина — это одна. Строка с одной страницы поэтому остаётся.
    """
    pages: dict[int, set[str]] = {}
    for block in blocks:
        pages.setdefault(block.page, set()).update(
            line for line in block.text.splitlines() if line
        )
    if len(pages) < 2:
        return set()
    counts: dict[str, int] = {}
    for lines in pages.values():
        for line in lines:
            counts[line] = counts.get(line, 0) + 1
    page_count = len(pages)
    return {line for line, count in counts.items() if count > page_count / 2}


def _join_hyphens(text: str) -> str:
    return re.sub(r"(\w)-\n(\w)", r"\1\2", text)
