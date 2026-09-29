"""Эмбеддинги. Имя модуля — из каркаса Хекслета."""

from app.services.embeddings import embed_documents, embed_query

__all__ = ["embed_documents", "embed_query"]
