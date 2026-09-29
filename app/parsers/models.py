from dataclasses import dataclass


@dataclass
class TextBlock:
    page: int
    section: str
    text: str
    kind: str = "text"

    @property
    def content(self) -> str:
        """Имя поля из каркаса Хекслета."""
        return self.text


@dataclass
class TableRow:
    page: int
    section: str
    article: str
    brand: str
    text: str


@dataclass
class FragmentDraft:
    page: int
    section: str
    article: str
    brand: str
    text: str


@dataclass
class ParseOutcome:
    fragments: list[FragmentDraft]
    reason: str | None = None
