import asyncio

from app.core.config import settings
from app.core.logging import get_logger
from app.repositories.search import missing_fragments, write_embeddings
from app.services import embeddings


async def index_document(document_id: str) -> int:
    """Досчитать векторы одного документа. Уже посчитанные не трогает."""
    return await index_missing(document_id)


async def index_missing(document_id: str | None = None) -> int:
    """Пачка для фрагментов без вектора. Повторный запуск ничего не пересчитывает."""
    rows = await missing_fragments(document_id)
    total = len(rows)
    if total == 0:
        get_logger().info("index_fragments", done=0, total=0, document_id=document_id)
        return 0
    done = 0
    batch = settings.embedding_batch_size
    for start in range(0, total, batch):
        chunk = rows[start : start + batch]
        vectors = await asyncio.to_thread(
            embeddings.embed_documents,
            [row["text"] for row in chunk],
        )
        pairs = [(row["id"], vector) for row, vector in zip(chunk, vectors, strict=True)]
        await write_embeddings(pairs)
        done += len(chunk)
        get_logger().info("index_fragments", done=done, total=total, document_id=document_id)
    return done
