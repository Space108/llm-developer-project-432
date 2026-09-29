"""Индексация фрагментов. Имя модуля — из каркаса Хекслета."""

from app.services.index import index_document, index_missing

__all__ = ["index_document", "index_missing"]
