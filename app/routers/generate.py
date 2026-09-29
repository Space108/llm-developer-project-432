from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.logging import bind_log, get_logger
from app.repositories.documents import load_document
from app.repositories.jobs import insert_generation_job
from app.schemas.jobs import GenerateRequest
from app.temporal.client import connect
from app.temporal.workflows import CardWorkflow

router = APIRouter()


@router.post("/generate-card", status_code=202)
async def generate_card(body: GenerateRequest, request: Request) -> JSONResponse:
    missing: list[str] = []
    for document_id in body.document_ids:
        row = await load_document(document_id)
        if row is None:
            missing.append(document_id)
    if missing:
        raise HTTPException(status_code=404, detail="unknown documents: " + ",".join(missing))
    job_id = await insert_generation_job(body.document_ids, body.product_hint)
    request_id = getattr(request.state, "request_id", "")
    bind_log(job_id=job_id, request_id=request_id)
    get_logger().info("handler", operation="generate-card", job_id=job_id, request_id=request_id)
    client = await connect()
    await client.start_workflow(
        CardWorkflow.run,
        args=[job_id, "", 3, body.document_ids, body.product_hint, request_id],
        id=job_id,
        task_queue=settings.temporal_task_queue,
    )
    return JSONResponse({"job_id": job_id, "status": "pending"}, status_code=202)
