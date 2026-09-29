from dataclasses import dataclass

from sqlalchemy import text

from app.core.config import settings
from app.core.db import connection

_FILTERS = """
    AND (CAST(:document_id AS text) IS NULL OR f.document_id = :document_id)
    AND (
        CAST(:document_ids AS text) IS NULL
        OR f.document_id = ANY(string_to_array(CAST(:document_ids AS text), ','))
    )
    AND (CAST(:page AS integer) IS NULL OR f.page = :page)
    AND (CAST(:section AS text) IS NULL OR f.section = :section)
    AND (CAST(:article AS text) IS NULL OR f.article = :article)
    AND (CAST(:brand AS text) IS NULL OR f.brand = :brand)
"""


@dataclass
class SearchFilters:
    document_id: str | None = None
    document_ids: tuple[str, ...] | None = None
    page: int | None = None
    section: str | None = None
    article: str | None = None
    brand: str | None = None


@dataclass
class FragmentHit:
    id: str
    document_id: str
    page: int
    section: str
    article: str
    brand: str
    text: str
    score: float


def _literal(vector: list[float]) -> str:
    return "[" + ",".join(repr(float(value)) for value in vector) + "]"


def _filter_params(filters: SearchFilters) -> dict:
    return {
        "document_id": filters.document_id,
        "document_ids": ",".join(filters.document_ids) if filters.document_ids else None,
        "page": filters.page,
        "section": filters.section,
        "article": filters.article,
        "brand": filters.brand,
    }


def _hits(rows) -> list[FragmentHit]:
    return [
        FragmentHit(
            id=row["id"],
            document_id=row["document_id"],
            page=row["page"],
            section=row["section"],
            article=row["article"],
            brand=row["brand"],
            text=row["text"],
            score=float(row["score"]),
        )
        for row in rows
    ]


async def missing_fragments(document_id: str | None = None) -> list[dict]:
    async with connection() as conn:
        result = await conn.execute(
            text(
                """
                SELECT id, text
                FROM fragments
                WHERE embedding IS NULL
                  AND (CAST(:document_id AS text) IS NULL OR document_id = :document_id)
                ORDER BY document_id, position
                """
            ),
            {"document_id": document_id},
        )
        rows = result.mappings().all()
    return [{"id": row["id"], "text": row["text"]} for row in rows]


async def write_embeddings(pairs: list[tuple[str, list[float]]]) -> None:
    """Запись пачки. Пустой список ничего не меняет."""
    if not pairs:
        return
    async with connection() as conn:
        await conn.execute(
            text(
                """
                UPDATE fragments
                SET embedding = CAST(:embedding AS vector)
                WHERE id = :id
                """
            ),
            [{"id": fragment_id, "embedding": _literal(vector)} for fragment_id, vector in pairs],
        )
        await conn.commit()


async def search_vectors(
    query_vector: list[float],
    filters: SearchFilters | None = None,
    *,
    limit: int | None = None,
    threshold: float | None = None,
) -> list[FragmentHit]:
    """Ближайшие по косинусу. Ниже порога не возвращаются."""
    filters = filters or SearchFilters()
    params = _filter_params(filters)
    params.update(
        {
            "query_vector": _literal(query_vector),
            "limit": settings.search_limit if limit is None else limit,
            "threshold": settings.relevance_threshold if threshold is None else threshold,
        }
    )
    async with connection() as conn:
        result = await conn.execute(
            text(
                f"""
                SELECT f.id, f.document_id, f.page, f.section, f.article, f.brand, f.text,
                       1 - (f.embedding <=> CAST(:query_vector AS vector)) AS score
                FROM fragments f
                WHERE f.embedding IS NOT NULL
                  AND 1 - (f.embedding <=> CAST(:query_vector AS vector)) >= :threshold
                  {_FILTERS}
                ORDER BY f.embedding <=> CAST(:query_vector AS vector)
                LIMIT :limit
                """
            ),
            params,
        )
        rows = result.mappings().all()
    return _hits(rows)


async def search_words(
    query_text: str,
    filters: SearchFilters | None = None,
    *,
    limit: int | None = None,
) -> list[FragmentHit]:
    """Ранжирование текстового поиска. Порог косинуса сюда не переносится: шкала другая."""
    filters = filters or SearchFilters()
    params = _filter_params(filters)
    params.update(
        {
            "query_text": query_text,
            "limit": settings.search_limit if limit is None else limit,
        }
    )
    async with connection() as conn:
        result = await conn.execute(
            text(
                f"""
                SELECT f.id, f.document_id, f.page, f.section, f.article, f.brand, f.text,
                       ts_rank(f.search_tsv, plainto_tsquery('russian', :query_text)) AS score
                FROM fragments f
                WHERE f.search_tsv @@ plainto_tsquery('russian', :query_text)
                  {_FILTERS}
                ORDER BY score DESC, f.id
                LIMIT :limit
                """
            ),
            params,
        )
        rows = result.mappings().all()
    return _hits(rows)


async def search_hybrid(
    query_text: str,
    query_vector: list[float],
    filters: SearchFilters | None = None,
    *,
    limit: int | None = None,
    threshold: float | None = None,
) -> list[FragmentHit]:
    """Слияние по обратным позициям. Фрагмент из обоих списков получает оба вклада."""
    filters = filters or SearchFilters()
    params = _filter_params(filters)
    params.update(
        {
            "query_text": query_text,
            "query_vector": _literal(query_vector),
            "limit": settings.search_limit if limit is None else limit,
            "threshold": settings.relevance_threshold if threshold is None else threshold,
            "rrf_k": settings.rrf_k,
        }
    )
    async with connection() as conn:
        result = await conn.execute(
            text(
                f"""
                WITH vector_hits AS (
                    SELECT id, rank
                    FROM (
                        SELECT f.id,
                               ROW_NUMBER() OVER (
                                   ORDER BY f.embedding <=> CAST(:query_vector AS vector)
                               ) AS rank
                        FROM fragments f
                        WHERE f.embedding IS NOT NULL
                          AND 1 - (f.embedding <=> CAST(:query_vector AS vector)) >= :threshold
                          {_FILTERS}
                    ) ranked
                    WHERE rank <= :limit
                ),
                word_hits AS (
                    SELECT id, rank
                    FROM (
                        SELECT f.id,
                               ROW_NUMBER() OVER (
                                   ORDER BY ts_rank(
                                       f.search_tsv, plainto_tsquery('russian', :query_text)
                                   ) DESC
                               ) AS rank
                        FROM fragments f
                        WHERE f.search_tsv @@ plainto_tsquery('russian', :query_text)
                          {_FILTERS}
                    ) ranked
                    WHERE rank <= :limit
                ),
                fused AS (
                    SELECT id, SUM(1.0 / (:rrf_k + rank)) AS score
                    FROM (
                        SELECT id, rank FROM vector_hits
                        UNION ALL
                        SELECT id, rank FROM word_hits
                    ) hits
                    GROUP BY id
                )
                SELECT f.id, f.document_id, f.page, f.section, f.article, f.brand, f.text,
                       fused.score
                FROM fused
                JOIN fragments f ON f.id = fused.id
                ORDER BY fused.score DESC, f.id
                LIMIT :limit
                """
            ),
            params,
        )
        rows = result.mappings().all()
    return _hits(rows)
