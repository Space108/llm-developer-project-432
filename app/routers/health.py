from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db

router = APIRouter()


@router.get("/health")
def liveness() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready")
async def readiness(session: AsyncSession = Depends(get_db)) -> dict[str, str]:
    try:
        await session.execute(text("SELECT 1"))
        result = await session.execute(
            text("SELECT extname FROM pg_extension WHERE extname = 'vector'")
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail="database unavailable") from exc
    if result.first() is None:
        raise HTTPException(status_code=503, detail="vector extension is not active")
    return {"status": "ok", "vector": "active"}
