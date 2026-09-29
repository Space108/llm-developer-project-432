"""Приёмка шага 10: загрузка выданных документов и генерация карточек."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import httpx
from app.core.db import close_pool, connection, open_pool, remember_loop
from app.repositories.jobs import load_job
from sqlalchemy import text

BASE = "http://127.0.0.1:8000"
DATA = Path("data")
DOCS = [
    "blender_passport.pdf",
    "blender_kp.docx",
    "kettle_manual.pdf",
    "kettle_spec.xlsx",
    "boiler_scan.pdf",
]
HINTS = {
    "blender_passport.pdf": "Какая мощность у блендера",
    "blender_kp.docx": "МиксерПро оптовые поставки",
    "kettle_manual.pdf": "чайник мощность",
    "kettle_spec.xlsx": "KTL-1700",
    "boiler_scan.pdf": "котёл",
}


async def _wait_doc(document_id: str, timeout: float = 600) -> dict:
    deadline = time.monotonic() + timeout
    last = None
    async with httpx.AsyncClient(timeout=60) as client:
        while time.monotonic() < deadline:
            response = await client.get(f"{BASE}/documents/{document_id}")
            response.raise_for_status()
            last = response.json()
            if last.get("status") in {"проиндексирован", "отказ"}:
                return last
            await asyncio.sleep(1)
    raise SystemExit(f"document timeout {document_id} last={last}")


async def _wait_job(job_id: str, timeout: float = 1200) -> dict:
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = await load_job(job_id)
        if last is not None and last["status"] == "ожидание":
            return last
        await asyncio.sleep(2)
    raise SystemExit(f"job timeout {job_id} last={last}")


async def _cost(job_id: str):
    async with connection() as conn:
        rows = (
            await conn.execute(
                text(
                    """
                    SELECT model, count(*) AS n, sum(cost) AS cost
                    FROM llm_calls WHERE job_id = :job
                    GROUP BY model ORDER BY model
                    """
                ),
                {"job": job_id},
            )
        ).mappings().all()
        total = (
            await conn.execute(
                text("SELECT coalesce(sum(cost), 0) FROM llm_calls WHERE job_id = :job"),
                {"job": job_id},
            )
        ).scalar_one()
    return float(total), [dict(row) for row in rows]


async def main() -> None:
    open_pool()
    remember_loop(asyncio.get_running_loop())
    results: list[dict] = []
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            health = await client.get(f"{BASE}/health/ready")
            print("ready", health.status_code, health.text)
            health.raise_for_status()

            doc_ids: dict[str, str] = {}
            for name in DOCS:
                path = DATA / name
                with path.open("rb") as handle:
                    response = await client.post(
                        f"{BASE}/documents/",
                        files={"file": (name, handle, "application/octet-stream")},
                    )
                response.raise_for_status()
                body = response.json()
                document_id = body["document_id"]
                doc_ids[name] = document_id
                print(f"uploaded {name} -> {document_id}")

            for name, document_id in doc_ids.items():
                info = await _wait_doc(document_id)
                print(
                    f"doc {name}: status={info.get('status')} "
                    f"fragments={info.get('fragment_count')} error={info.get('error')}"
                )

            for name, document_id in doc_ids.items():
                if name == "boiler_scan.pdf":
                    # отказ на разборе — генерация всё равно для отчёта
                    pass
                hint = HINTS[name]
                response = await client.post(
                    f"{BASE}/generate-card",
                    json={"document_ids": [document_id], "product_hint": hint},
                )
                response.raise_for_status()
                job_id = response.json()["job_id"]
                print(f"job {name}: {job_id} hint={hint!r}")
                waiting = await _wait_job(job_id)
                card = waiting["result"]
                security = card.security.model_dump() if card and card.security else None
                cost, by_model = await _cost(job_id)
                card_json = card.model_dump_json() if card else ""
                row = {
                    "filename": name,
                    "document_id": document_id,
                    "job_id": job_id,
                    "status": waiting["status"],
                    "error": waiting.get("error"),
                    "title": card.title if card else "",
                    "confidence": card.confidence if card else None,
                    "missing_fields": card.missing_fields if card else [],
                    "sources": [item.chunk_id for item in card.sources] if card else [],
                    "security": security,
                    "cost": cost,
                    "cost_by_model": by_model,
                    "has_injection_price": "1 рубль" in card_json.lower(),
                    "has_raw_phone": "+7 926 555-14-08" in card_json,
                }
                results.append(row)
                print(
                    f"  title={row['title']!r} conf={row['confidence']} "
                    f"cost={row['cost']:.8f} sec={bool(security)} "
                    f"err={row['error']}"
                )
                await client.post(f"{BASE}/jobs/{job_id}/approve")

        out = Path("data/reports/acceptance_cards.json")
        out.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(results, ensure_ascii=False, indent=2, default=str)
        out.write_text(payload, encoding="utf-8")
        print("wrote", out)
        avg_cost = sum(item["cost"] for item in results) / max(len(results), 1)
        print(f"avg_card_cost={avg_cost:.8f}")
    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
