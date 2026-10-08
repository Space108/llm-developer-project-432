import hashlib
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from temporalio.exceptions import WorkflowAlreadyStartedError

from app.core.config import settings
from app.core.logging import get_logger
from app.repositories.documents import (
    add_document,
    count_fragments,
    discard_upload,
    documents,
    find_document_by_hash,
    insert_document,
    load_document,
)
from app.temporal.client import connect
from app.temporal.workflows import DocumentWorkflow

router = APIRouter()

ALLOWED_EXTENSIONS = {".pdf", ".docx", ".xlsx"}


@router.post("/documents", status_code=202, include_in_schema=False)
@router.post("/documents/", status_code=202)
async def upload_document(file: UploadFile = File(...)) -> JSONResponse:
    filename = file.filename or ""
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="unsupported extension")
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="empty file")
    if len(content) > settings.max_upload_bytes:
        raise HTTPException(status_code=400, detail="file is too large")
    digest = hashlib.sha256(content).hexdigest()
    existing = await find_document_by_hash(digest)
    if existing is not None:
        return await _existing_response(existing)
    document = add_document(filename, content)
    document.status = "новый"
    document_id, created = await insert_document(
        document.document_id,
        filename,
        digest,
        str(document.path),
    )
    if created:
        await _start_parse(document_id)
        return JSONResponse({"document_id": document_id, "status": "новый"}, status_code=202)
    # Тот же файл успел записать параллельный запрос: наш экземпляр на диске лишний.
    discard_upload(document)
    row = await find_document_by_hash(digest)
    if row is None:
        return JSONResponse({"document_id": document_id, "status": "новый"}, status_code=202)
    return await _existing_response(row)


@router.get("/documents/{document_id}")
async def read_document(document_id: str) -> dict:
    # База — источник правды; запись из памяти читается, только если в базе документа нет
    # или база недоступна.
    try:
        row = await load_document(document_id)
    except Exception:
        if documents.get(document_id) is None:
            raise
        get_logger().warning(
            "document_read_from_memory", document_id=document_id, reason="база недоступна"
        )
        row = None
    if row is None:
        memory = documents.get(document_id)
        if memory is None:
            raise HTTPException(status_code=404, detail="document not found")
        return {
            "document_id": document_id,
            "status": memory.status,
            "fragment_count": 0,
            "error": None,
        }
    return {
        "document_id": document_id,
        "status": row["status"],
        "fragment_count": await count_fragments(document_id),
        "error": row["error"],
    }


async def _existing_response(row: dict) -> JSONResponse:
    """Файл уже в базе: разбор запускается, только если он ещё не начинался."""
    if row["status"] == "новый":
        await _start_parse(str(row["id"]))
    return JSONResponse({"document_id": row["id"], "status": row["status"]}, status_code=202)


async def _start_parse(document_id: str) -> None:
    client = await connect()
    try:
        await client.start_workflow(
            DocumentWorkflow.run,
            args=[document_id],
            id=document_id,
            task_queue=settings.temporal_task_queue,
        )
    except WorkflowAlreadyStartedError:
        return
