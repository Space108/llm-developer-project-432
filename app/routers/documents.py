import hashlib
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from temporalio.exceptions import WorkflowAlreadyStartedError

from app.core.config import settings
from app.repositories.documents import (
    add_document,
    count_fragments,
    documents,
    find_document_by_hash,
    insert_document,
    load_document,
)
from app.temporal.client import connect
from app.temporal.workflows import DocumentWorkflow

router = APIRouter()

ALLOWED_EXTENSIONS = {".pdf", ".docx", ".xlsx"}


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
        if existing["status"] == "новый":
            await _start_parse(str(existing["id"]))
        return JSONResponse(
            {"document_id": existing["id"], "status": existing["status"]},
            status_code=202,
        )
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
    else:
        row = await find_document_by_hash(digest)
        if row is not None and row["status"] == "новый":
            await _start_parse(str(row["id"]))
        if row is not None:
            return JSONResponse(
                {"document_id": row["id"], "status": row["status"]},
                status_code=202,
            )
    return JSONResponse({"document_id": document_id, "status": "новый"}, status_code=202)


@router.get("/documents/{document_id}")
async def read_document(document_id: str) -> dict:
    row = await load_document(document_id)
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
