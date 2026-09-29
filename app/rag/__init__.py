"""Пакет RAG: контекст, эмбеддинги, поиск, индексация."""

from app.rag.context import BuiltContext, build_context, context_chunk_ids, format_source
from app.rag.embedder import embed_documents, embed_query
from app.rag.reindex import index_document, index_missing
from app.rag.retrieval import retrieve_context

__all__ = [
    "BuiltContext",
    "build_context",
    "context_chunk_ids",
    "embed_documents",
    "embed_query",
    "format_source",
    "index_document",
    "index_missing",
    "retrieve_context",
]
