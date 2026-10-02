import secrets
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import text

from app.core.db import connection
from app.parsers.models import FragmentDraft

UPLOADS = Path("data/uploads")


def new_id() -> str:
    return secrets.token_hex(6)


@dataclass
class Document:
    document_id: str
    filename: str
    path: Path
    status: str = "new"


documents: dict[str, Document] = {}


def add_document(filename: str, content: bytes) -> Document:
    UPLOADS.mkdir(parents=True, exist_ok=True)
    document_id = new_id()
    suffix = Path(filename).suffix.lower()
    path = UPLOADS / f"{document_id}{suffix}"
    path.write_bytes(content)
    document = Document(document_id=document_id, filename=filename, path=path)
    documents[document_id] = document
    return document


async def find_document_by_hash(content_hash: str) -> dict | None:
    async with connection() as conn:
        result = await conn.execute(
            text(
                """
                SELECT id, status, error
                FROM documents WHERE content_hash = :content_hash
                """
            ),
            {"content_hash": content_hash},
        )
        row = result.mappings().first()
    if row is None:
        return None
    return {"id": row["id"], "status": row["status"], "error": row["error"]}


async def find_document_id_by_filename(filename: str) -> str | None:
    """Идентификатор уже загруженного документа с таким именем файла."""
    async with connection() as conn:
        result = await conn.execute(
            text("SELECT id FROM documents WHERE filename = :name LIMIT 1"),
            {"name": filename},
        )
        row = result.first()
    return None if row is None else str(row[0])


async def insert_document(
    document_id: str,
    filename: str,
    content_hash: str,
    path: str,
) -> tuple[str, bool]:
    """Новая строка или уже существующая по хешу файла."""
    existing = await find_document_by_hash(content_hash)
    if existing is not None:
        return str(existing["id"]), False
    async with connection() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO documents (id, filename, content_hash, status, path)
                VALUES (:id, :filename, :content_hash, 'новый', :path)
                """
            ),
            {
                "id": document_id,
                "filename": filename,
                "content_hash": content_hash,
                "path": path,
            },
        )
        await conn.commit()
    return document_id, True


async def load_document(document_id: str) -> dict | None:
    async with connection() as conn:
        result = await conn.execute(
            text(
                """
                SELECT id, filename, status, error, path
                FROM documents WHERE id = :document_id
                """
            ),
            {"document_id": document_id},
        )
        row = result.mappings().first()
    if row is None:
        return None
    return {
        "id": row["id"],
        "filename": row["filename"],
        "status": row["status"],
        "error": row["error"],
        "path": row["path"],
    }


async def update_document_status(
    document_id: str,
    status: str,
    *,
    error: str | None = None,
) -> None:
    async with connection() as conn:
        await conn.execute(
            text(
                """
                UPDATE documents
                SET status = :status, error = :error, updated_at = now()
                WHERE id = :document_id
                """
            ),
            {"status": status, "error": error, "document_id": document_id},
        )
        await conn.commit()


async def replace_fragments(document_id: str, fragments: list[FragmentDraft]) -> None:
    async with connection() as conn:
        await conn.execute(
            text("DELETE FROM fragments WHERE document_id = :document_id"),
            {"document_id": document_id},
        )
        for position, fragment in enumerate(fragments):
            await conn.execute(
                text(
                    """
                    INSERT INTO fragments (
                        id, document_id, page, section, article, brand, text, position
                    )
                    VALUES (
                        :id, :document_id, :page, :section, :article, :brand, :text, :position
                    )
                    """
                ),
                {
                    "id": new_id(),
                    "document_id": document_id,
                    "page": fragment.page,
                    "section": fragment.section,
                    "article": fragment.article,
                    "brand": fragment.brand,
                    "text": fragment.text,
                    "position": position,
                },
            )
        await conn.commit()


async def existing_fragment_ids(ids: list[str]) -> set[str]:
    """Какие из переданных идентификаторов есть в базе."""
    if not ids:
        return set()
    params = {f"id{index}": value for index, value in enumerate(ids)}
    holders = ", ".join(f":id{index}" for index in range(len(ids)))
    async with connection() as conn:
        result = await conn.execute(
            text(f"SELECT id FROM fragments WHERE id IN ({holders})"),
            params,
        )
        return {str(row[0]) for row in result}


async def count_fragments(document_id: str) -> int:
    async with connection() as conn:
        result = await conn.execute(
            text("SELECT count(*) FROM fragments WHERE document_id = :document_id"),
            {"document_id": document_id},
        )
        return int(result.scalar_one())
