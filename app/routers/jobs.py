from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from temporalio.service import RPCError, RPCStatusCode

from app.core.config import settings
from app.core.logging import bind_log, get_logger
from app.repositories.jobs import insert_job, jobs, load_job
from app.schemas.cards import SupplierText
from app.schemas.jobs import JobView
from app.temporal.client import connect
from app.temporal.workflows import CardWorkflow

router = APIRouter()


@router.post("/jobs", status_code=202)
async def create_job(
    body: SupplierText,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> JSONResponse:
    job_id, created = await insert_job(body.supplier_text, idempotency_key)
    request_id = getattr(request.state, "request_id", "")
    bind_log(job_id=job_id, request_id=request_id)
    get_logger().info("handler", operation="create-job", job_id=job_id, request_id=request_id)
    if created:
        client = await connect()
        await client.start_workflow(
            CardWorkflow.run,
            args=[job_id, body.supplier_text, 3, None, "", request_id],
            id=job_id,
            task_queue=settings.temporal_task_queue,
        )
    return JSONResponse({"id": job_id, "status": "pending"}, status_code=202)


@router.get("/jobs/{job_id}", response_model=JobView)
async def read_job(job_id: str) -> JobView:
    # Источник правды — база. Словарь из раннего шага — запасной путь: когда задачи нет в базе
    # или база недоступна. Вторая причина нужна, чтобы запись из памяти не пропала вместе с ней.
    try:
        row = await load_job(job_id)
    except Exception:
        memory = jobs.get(job_id)
        if memory is None:
            raise
        get_logger().warning("job_read_from_memory", job_id=job_id, reason="база недоступна")
        return JobView(status=memory.status, result=memory.result)
    if row is None:
        memory = jobs.get(job_id)
        if memory is None:
            raise HTTPException(status_code=404, detail="job not found")
        return JobView(status=memory.status, result=memory.result)
    result = row["result"]
    return JobView(
        status=row["status"],
        result=result,
        attempts=row["attempts"],
        error=row["error"],
    )


@router.post("/jobs/{job_id}/approve")
async def approve_job(job_id: str) -> dict[str, str]:
    await _send_signal(job_id, CardWorkflow.approve)
    return {"id": job_id, "signal": "approve"}


@router.post("/jobs/{job_id}/reject")
async def reject_job(job_id: str) -> dict[str, str]:
    await _send_signal(job_id, CardWorkflow.reject)
    return {"id": job_id, "signal": "reject"}


async def _send_signal(job_id: str, signal) -> None:
    """Сигнал процессу. Нет такого процесса или он уже завершён — 404, а не 500."""
    client = await connect()
    try:
        await client.get_workflow_handle(job_id).signal(signal)
    except RPCError as exc:
        if exc.status == RPCStatusCode.NOT_FOUND:
            raise HTTPException(
                status_code=404,
                detail="job not found or already finished",
            ) from exc
        raise
