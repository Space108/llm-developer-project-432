import asyncio
from pathlib import Path

from app.core.config import settings
from app.core.logging import get_logger
from app.repositories.documents import load_document
from app.repositories.search import FragmentHit, SearchFilters, search_hybrid
from app.services.security import ScreenedContext, screen_hits


async def retrieve_context(document_ids: list[str], product_hint: str) -> ScreenedContext:
    """Гибридный поиск, маскирование и отсев инъекций среди выбранных документов."""
    hint = product_hint.strip()
    if hint:
        hits = await _search(hint, SearchFilters(document_ids=tuple(document_ids)))
    else:
        hits = []
        for document_id in document_ids:
            row = await load_document(document_id)
            title = _document_title(str(row["filename"]) if row is not None else document_id)
            found = await _search(title, SearchFilters(document_id=document_id))
            hits.extend(found)
        hits.sort(key=lambda item: item.score, reverse=True)
    screened = await asyncio.to_thread(screen_hits, hits, settings.context_size_limit)
    get_logger().info(
        "context_built",
        size=screened.built.size,
        fragments=len(screened.built.fragments),
        documents=len(document_ids),
        blocked=screened.blocked,
        excluded=len(screened.security.excluded),
        masked=len(screened.security.masked),
    )
    return screened


def _document_title(filename: str) -> str:
    """Заголовок для поиска: имя файла без расширения."""
    return Path(filename).stem.replace("_", " ").replace("-", " ").strip() or filename


async def _search(query: str, filters: SearchFilters) -> list[FragmentHit]:
    from app.services import embeddings

    vector = await asyncio.to_thread(embeddings.embed_query, query)
    return await search_hybrid(query, vector, filters)
