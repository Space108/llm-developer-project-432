"""Фрагменты в базе. Путь каркаса Хекслета."""

from app.repositories.documents import count_fragments, existing_fragment_ids, replace_fragments
from app.services.embeddings import embed_query

__all__ = [
    "count_fragments",
    "embed_query",
    "existing_fragment_ids",
    "get_chunks_by_ids",
    "replace_fragments",
    "search_fts",
    "search_hybrid",
    "search_vector",
]


def _as_list(vector: object) -> list[float]:
    if hasattr(vector, "tolist"):
        vector = vector.tolist()
    return [float(item) for item in vector]  # type: ignore[arg-type]


def _vector_literal(values: list[float]) -> str:
    return "[" + ",".join(f"{item:.8f}" for item in values) + "]"


def _rows(conn, sql: str, params: list[object]) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        fetched = cur.fetchall()
    return [{"chunk_id": row[0], "score": float(row[1])} for row in fetched]


def search_vector(
    conn,
    query: str,
    top_k: int = 5,
    threshold: float = 0.0,
    doc_ids: list[str] | None = None,
) -> list[dict]:
    """Ближайшие чанки по косинусу. embed_query берётся из этого модуля — его патчат тесты."""
    literal = _vector_literal(_as_list(embed_query(query)))
    sql = """
        SELECT id, 1 - (embedding <=> %s::vector) AS score
        FROM chunks
        WHERE embedding IS NOT NULL
          AND 1 - (embedding <=> %s::vector) >= %s
    """
    params: list[object] = [literal, literal, threshold]
    if doc_ids is not None:
        sql += " AND doc_id = ANY(%s)"
        params.append(list(doc_ids))
    sql += " ORDER BY embedding <=> %s::vector LIMIT %s"
    params.extend([literal, top_k])
    return _rows(conn, sql, params)


def search_fts(
    conn,
    query: str,
    top_k: int = 5,
    doc_ids: list[str] | None = None,
) -> list[dict]:
    """Точные слова по русскому полнотекстовому индексу колонки text."""
    sql = """
        SELECT id,
               ts_rank(to_tsvector('russian', "text"), plainto_tsquery('russian', %s)) AS score
        FROM chunks
        WHERE to_tsvector('russian', "text") @@ plainto_tsquery('russian', %s)
    """
    params: list[object] = [query, query]
    if doc_ids is not None:
        sql += " AND doc_id = ANY(%s)"
        params.append(list(doc_ids))
    sql += " ORDER BY score DESC, id LIMIT %s"
    params.append(top_k)
    return _rows(conn, sql, params)


def search_hybrid(
    conn,
    query: str,
    top_k: int = 5,
    threshold: float = 0.0,
    doc_ids: list[str] | None = None,
) -> list[dict]:
    """Обратные ранги вектора и полнотекста. Совпадение в обоих списках выше."""
    vector_hits = search_vector(conn, query, top_k=top_k, threshold=threshold, doc_ids=doc_ids)
    word_hits = search_fts(conn, query, top_k=max(top_k, 50), doc_ids=doc_ids)
    rrf_k = 60
    scores: dict[str, float] = {}
    for rank, hit in enumerate(vector_hits, start=1):
        scores[hit["chunk_id"]] = scores.get(hit["chunk_id"], 0.0) + 1.0 / (rrf_k + rank)
    for rank, hit in enumerate(word_hits, start=1):
        scores[hit["chunk_id"]] = scores.get(hit["chunk_id"], 0.0) + 1.0 / (rrf_k + rank)
    ordered = sorted(scores, key=lambda chunk_id: (-scores[chunk_id], chunk_id))[:top_k]
    return [{"chunk_id": chunk_id, "score": scores[chunk_id]} for chunk_id in ordered]


def get_chunks_by_ids(conn, ids: list[str]) -> dict[str, str]:
    """Идентификаторы, которые реально лежат в chunks. Проверка через `in`."""
    if not ids:
        return {}
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM chunks WHERE id = ANY(%s)", (list(ids),))
        found = {row[0] for row in cur.fetchall()}
    return {chunk_id: chunk_id for chunk_id in found}
