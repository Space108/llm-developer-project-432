from pathlib import Path

from app.core.config import settings
from app.parsers.docx import parse_docx
from app.parsers.models import ParseOutcome
from app.parsers.pdf import parse_pdf
from app.parsers.xlsx import parse_xlsx
from app.services.chunk import chunk_blocks, rows_to_fragments
from app.services.normalize import normalize_blocks

NO_TEXT_LAYER = "нет текстового слоя"


def prepare_fragments(path: Path) -> ParseOutcome:
    """Разбор, нормализация и резка. Пустой результат без причины не возвращается."""
    suffix = path.suffix.lower()
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
