import json
import uuid
from dataclasses import dataclass

from sqlalchemy import text

from app.core.db import connection
from app.repositories.documents import documents, new_id
from app.schemas.cards import ProductCard

CARD_FIELDS = [
    "title",
    "description",
    "characteristics",
    "benefits",
    "seo",
]


@dataclass
class Job:
    job_id: str
    document_ids: list[str]
    product_hint: str
    status: str = "pending"
    result: ProductCard | None = None


jobs: dict[str, Job] = {}


def add_job(document_ids: list[str], product_hint: str) -> Job:
    missing = [item for item in document_ids if item not in documents]
    if missing:
        raise KeyError(",".join(missing))
    job = Job(
        job_id=new_id(),
        document_ids=document_ids,
        product_hint=product_hint,
    )
    jobs[job.job_id] = job
    return job


def finish_empty(job: Job) -> Job:
    """Черновик без выдуманных фактов: полей из документов ещё нет."""
    job.result = ProductCard(
        missing_fields=list(CARD_FIELDS),
        confidence=0,
    )
    job.status = "approved"
    return job


async def insert_generation_job(document_ids: list[str], product_hint: str) -> str:
    """Задача генерации по списку документов. Ключа идемпотентности у этой ручки нет."""
    job_id = uuid.uuid4().hex[:12]
    payload = json.dumps(
        {"document_ids": document_ids, "product_hint": product_hint},
        ensure_ascii=False,
    )
    async with connection() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO jobs (id, payload)
                VALUES (:id, CAST(:payload AS jsonb))
                """
            ),
            {"id": job_id, "payload": payload},
        )
        await conn.commit()
    return job_id


async def _job_id_by_key(conn, idempotency_key: str) -> str | None:
    found = await conn.execute(
        text("SELECT id FROM jobs WHERE idempotency_key = :key"),
        {"key": idempotency_key},
    )
    row = found.first()
    return None if row is None else str(row[0])


async def insert_job(supplier_text: str, idempotency_key: str | None) -> tuple[str, bool]:
    """Новая строка или уже существующая по ключу идемпотентности.

    Два одновременных запроса с одним ключом дают одну задачу: проигравший получает её id.
    """
    async with connection() as conn:
        if idempotency_key:
            existing = await _job_id_by_key(conn, idempotency_key)
            if existing is not None:
                return existing, False
        job_id = uuid.uuid4().hex[:12]
        inserted = await conn.execute(
            text(
                """
                INSERT INTO jobs (id, idempotency_key, payload)
                VALUES (:id, :key, CAST(:payload AS jsonb))
                ON CONFLICT (idempotency_key) DO NOTHING
                RETURNING id
                """
            ),
            {
                "id": job_id,
                "key": idempotency_key,
                "payload": json.dumps({"supplier_text": supplier_text}, ensure_ascii=False),
            },
        )
        created = inserted.first() is not None
        await conn.commit()
        if created:
            return job_id, True
        if idempotency_key:
            existing = await _job_id_by_key(conn, idempotency_key)
            if existing is not None:
                return existing, False
        raise RuntimeError("задача не создана и не найдена по ключу идемпотентности")


async def update_job_status(
    job_id: str,
    status: str,
    *,
    result_json: str | None = None,
    error: str | None = None,
    bump_attempts: bool = False,
) -> None:
    sets = ["status = :status", "updated_at = now()"]
    params: dict[str, object] = {"status": status, "job_id": job_id}
    if result_json is not None:
        sets.append("result = CAST(:result AS jsonb)")
        params["result"] = result_json
    if error is not None:
        sets.append("error = :error")
        params["error"] = error
    if bump_attempts:
        sets.append("attempts = attempts + 1")
    async with connection() as conn:
        await conn.execute(
            text(f"UPDATE jobs SET {', '.join(sets)} WHERE id = :job_id"),
            params,
        )
        await conn.commit()


async def load_job(job_id: str) -> dict | None:
    async with connection() as conn:
        result = await conn.execute(
            text(
                """
                SELECT id, status, attempts, result, error
                FROM jobs WHERE id = :job_id
                """
            ),
            {"job_id": job_id},
        )
        row = result.mappings().first()
    if row is None:
        return None
    raw_result = row["result"]
    card = None
    if isinstance(raw_result, str):
        card = ProductCard.model_validate_json(raw_result)
    elif isinstance(raw_result, dict):
        card = ProductCard.model_validate(raw_result)
    return {
        "id": row["id"],
        "status": row["status"],
        "attempts": row["attempts"],
        "result": card,
        "error": row["error"],
    }
