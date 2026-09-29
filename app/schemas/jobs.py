from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.cards import ProductCard


class DocumentCreated(BaseModel):
    document_id: str
    status: str = "новый"


class DocumentView(BaseModel):
    document_id: str
    status: str
    fragment_count: int = 0
    error: str | None = None


class GenerateRequest(BaseModel):
    document_ids: list[str] = Field(min_length=1)
    product_hint: str = ""


class JobCreated(BaseModel):
    job_id: str
    status: Literal["pending", "approved", "failed"]


class JobView(BaseModel):
    status: str
    result: ProductCard | None = None
    attempts: int = 0
    error: str | None = None
