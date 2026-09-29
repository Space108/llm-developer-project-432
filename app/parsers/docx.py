from pathlib import Path

from docx import Document

from app.parsers.models import TextBlock


def parse_docx(path: Path) -> list[TextBlock]:
    """Параграфы docx. Заголовок задаёт секцию, явный разрыв страницы увеличивает номер."""
    document = Document(str(path))
    page = 1
    section = ""
    blocks: list[TextBlock] = []
    for paragraph in document.paragraphs:
        style = paragraph.style.name if paragraph.style is not None else ""
        text = paragraph.text or ""
        if style.startswith("Heading") or style.startswith("Заголовок"):
            section = text.strip()
        blocks.append(TextBlock(page=page, section=section, text=text))
        if _has_page_break(paragraph):
            page += 1
    return blocks


def _has_page_break(paragraph) -> bool:
    xml = paragraph._element.xml
    return 'w:type="page"' in xml or "w:type='page'" in xml
