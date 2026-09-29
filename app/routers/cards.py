from fastapi import APIRouter

from app.schemas.cards import CardDraft, SupplierText
from app.services.pipeline import run_pipeline

router = APIRouter()


@router.post("/cards", response_model=CardDraft)
def create_card(body: SupplierText) -> CardDraft:
    draft, _attempts, _verdict = run_pipeline(body.supplier_text)
    return draft
