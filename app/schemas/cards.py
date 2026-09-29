from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

TITLE_MAX_LENGTH = 60


class SourceRef(BaseModel):
    chunk_id: str
    page: int | None = None
    section: str | None = None


class SeoBlock(BaseModel):
    title: str = ""
    description: str = ""


Seo = SeoBlock


class SupplierFacts(BaseModel):
    product_name: str
    characteristics: dict[str, str] = Field(default_factory=dict)
    missing_fields: list[str] = Field(default_factory=list)


class SecurityFinding(BaseModel):
    kind: str
    label: str
    fragment_id: str | None = None


class SecurityReport(BaseModel):
    masked: list[SecurityFinding] = Field(default_factory=list)
    excluded: list[SecurityFinding] = Field(default_factory=list)
    blocked: bool = False
    block_reason: str | None = None
    suspicious_chunks: list[str] = Field(default_factory=list)

    @property
    def needs_review(self) -> bool:
        from app.core.config import settings

        return len(self.suspicious_chunks) > settings.suspicious_chunk_limit

    def __bool__(self) -> bool:
        return bool(self.masked or self.excluded or self.blocked or self.suspicious_chunks)

class CardRules(BaseModel):
    title: str = Field(default="", max_length=TITLE_MAX_LENGTH)
    description: str = ""
    characteristics: dict[str, str] = Field(default_factory=dict)
    benefits: list[str] = Field(default_factory=list)
    seo: SeoBlock = Field(default_factory=SeoBlock)
    sources: list[SourceRef] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0, ge=0, le=1)
    security: SecurityReport | None = None

    @field_validator("characteristics", mode="before")
    @classmethod
    def drop_empty_characteristics(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        return {key: item for key, item in value.items() if _filled(item)}

    @model_validator(mode="after")
    def field_not_in_both_lists(self) -> "CardRules":
        overlap = sorted(set(self.characteristics) & set(self.missing_fields))
        if overlap:
            raise ValueError("поле в двух списках: " + ", ".join(overlap))
        return self


class CardDraft(CardRules):
    title: str = Field(max_length=TITLE_MAX_LENGTH)
    description: str


class CritiqueReport(BaseModel):
    verdict: Literal["approve", "regenerate"]
    issues: list[str] = Field(default_factory=list)


class JudgeVerdict(BaseModel):
    supported: bool
    unsupported_claims: list[str] = Field(default_factory=list)


class SupplierText(BaseModel):
    supplier_text: str


class ProductCard(CardRules):
    pass


def _filled(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    return True
