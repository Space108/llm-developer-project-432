from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.repositories.health import vector_extension_active

router = APIRouter()


@router.get("/health")
def liveness() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready")
async def readiness(session: AsyncSession = Depends(get_db)) -> dict[str, str]:
    try:
        vector_active = await vector_extension_active(session)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="database unavailable") from exc
    if not vector_active:
        raise HTTPException(status_code=503, detail="vector extension is not active")
    return {"status": "ok", "vector": "active"}
