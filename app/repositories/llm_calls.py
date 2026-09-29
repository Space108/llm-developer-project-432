from decimal import Decimal

from sqlalchemy import text

from app.core.db import connection
from app.repositories.documents import new_id


async def insert_call(
    *,
    job_id: str | None,
    request_id: str | None,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    cost: Decimal,
    duration_ms: int,
) -> str:
    call_id = new_id()
    async with connection() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO llm_calls (
                    id, job_id, request_id, model,
                    prompt_tokens, completion_tokens, cost, duration_ms
                )
                VALUES (
                    :id, :job_id, :request_id, :model,
                    :prompt_tokens, :completion_tokens, :cost, :duration_ms
                )
                """
            ),
            {
                "id": call_id,
                "job_id": job_id,
                "request_id": request_id,
                "model": model,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "cost": str(cost),
                "duration_ms": duration_ms,
            },
        )
        await conn.commit()
    return call_id


async def sum_by_model() -> list[dict]:
    async with connection() as conn:
        result = await conn.execute(
            text(
                """
                SELECT model,
                       count(*) AS calls,
                       sum(cost) AS total_cost
                FROM llm_calls
                GROUP BY model
                ORDER BY model
                """
            )
        )
        rows = result.mappings().all()
    return [
        {
            "model": row["model"],
            "calls": int(row["calls"]),
            "total_cost": Decimal(str(row["total_cost"] or 0)),
        }
        for row in rows
    ]


async def cost_for_job(job_id: str) -> Decimal:
    async with connection() as conn:
        result = await conn.execute(
            text("SELECT coalesce(sum(cost), 0) FROM llm_calls WHERE job_id = :job_id"),
            {"job_id": job_id},
        )
        value = result.scalar_one()
    return Decimal(str(value or 0))
