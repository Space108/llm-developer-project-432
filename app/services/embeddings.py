import threading

from app.core.config import settings

_model = None
_lock = threading.Lock()


def embed_documents(texts: list[str]) -> list[list[float]]:
    """Векторы документов пачкой. Префикс — из карточки модели."""
    return _encode(texts, settings.embedding_document_prefix)


def embed_query(text: str) -> list[float]:
    """Вектор запроса. Префикс запроса другой, чем у документа."""
    rows = _encode([text], settings.embedding_query_prefix)
    return rows[0]


def _encode(texts: list[str], prompt: str) -> list[list[float]]:
    if not texts:
        return []
    with _lock:
        model = _load_model()
        array = model.encode(
            texts,
            prompt=prompt,
            batch_size=settings.embedding_batch_size,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
    if getattr(array, "ndim", 1) == 1:
        return [array.tolist()]
    return [row.tolist() for row in array]


def _load_model():
    """Один экземпляр на процесс. Считает на процессоре, float16 карточка не разрешает."""
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer

        _model = SentenceTransformer(settings.embedding_model, device="cpu")
    return _model
