"""Схему определяют только миграции, поэтому порядок запуска не важен (ADR 0005)."""

from concurrent.futures import ThreadPoolExecutor

import psycopg
import pytest
from app.core.db import close_pool, connection, open_pool
from app.core.migrate import apply_migrations, migration_files


def _insert_scaffold_rows(conn) -> None:
    """Вставки в форме каркаса: filename, content_hash, status и path не заданы."""
    with conn.cursor() as cur:
        cur.execute("INSERT INTO documents (id, kind) VALUES ('doc-a', 'pdf')")
        cur.execute("INSERT INTO documents (id, filename, kind) VALUES ('doc-b', 'b.pdf', 'pdf')")
        cur.execute(
            "INSERT INTO chunks (id, doc_id, ordinal, text, metadata) "
            "VALUES ('c1', 'doc-a', 0, 'текст', '{}'::jsonb)"
        )
        cur.execute("SELECT count(*) FROM documents")
        assert cur.fetchone()[0] == 2


def _versions(conn) -> list[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT version FROM schema_migrations ORDER BY version")
        return [row[0] for row in cur.fetchall()]


async def _apply_with_pool() -> list[str]:
    open_pool()
    try:
        return await apply_migrations()
    finally:
        await close_pool()


async def test_connection_first_then_migrate(scratch_database) -> None:
    """Тесты стартуют раньше `python -m app.core.migrate`."""
    with connection() as conn:
        _insert_scaffold_rows(conn)
        assert _versions(conn) == [path.name for path in migration_files()]
    assert await _apply_with_pool() == []


async def test_migrate_first_then_connection(scratch_database) -> None:
    """Миграции применены заранее: вставки без path больше не падают с NotNullViolation."""
    assert await _apply_with_pool() == [path.name for path in migration_files()]
    with connection() as conn:
        _insert_scaffold_rows(conn)


async def test_repeated_connections_do_not_reapply(scratch_database) -> None:
    with connection() as conn:
        first = _versions(conn)
    with connection() as conn:
        assert _versions(conn) == first


async def test_content_hash_stays_unique(scratch_database) -> None:
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO documents (id, content_hash) VALUES ('a', 'same')")
            with pytest.raises(psycopg.errors.UniqueViolation):
                cur.execute("INSERT INTO documents (id, content_hash) VALUES ('b', 'same')")
        conn.rollback()


async def test_parallel_first_connections_apply_once(scratch_database) -> None:
    """Несколько процессов стартуют на пустой базе: миграции идут по очереди."""

    def open_and_list() -> list[str]:
        with connection() as conn:
            return _versions(conn)

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: open_and_list(), range(4)))
    expected = [path.name for path in migration_files()]
    assert results == [expected] * 4
