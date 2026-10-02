import uuid

import asyncpg
import pytest
from app.core.config import settings
from app.core.db import async_database_url, close_pool, open_pool
from app.core.migrate import apply_migrations, format_report, migration_files, split_sql
from sqlalchemy.engine import make_url


def test_split_sql_keeps_dollar_quotes() -> None:
    script = """
    CREATE EXTENSION IF NOT EXISTS vector;
    CREATE FUNCTION keep() RETURNS text LANGUAGE sql AS $$ select 'a;b'; $$;
    """
    statements = split_sql(script)
    assert len(statements) == 2
    assert "CREATE EXTENSION" in statements[0]
    assert "select 'a;b'" in statements[1]


def test_report_applied_and_empty() -> None:
    assert format_report(["0001_vector.sql"]) == "применена 0001_vector.sql"
    assert format_report([]) == "новых нет"


async def test_migrations_apply_on_an_empty_database_then_do_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = make_url(async_database_url(settings.database_url))
    scratch = f"migrate_check_{uuid.uuid4().hex[:8]}"
    admin_dsn = url.set(drivername="postgresql", database="postgres").render_as_string(
        hide_password=False
    )
    try:
        admin = await asyncpg.connect(admin_dsn)
        await admin.execute(f'CREATE DATABASE "{scratch}"')
    except Exception as exc:
        pytest.skip(f"postgres unavailable or no right to create a database: {exc}")
    try:
        scratch_url = url.set(database=scratch).render_as_string(hide_password=False)
        monkeypatch.setattr(settings, "database_url", scratch_url)
        await close_pool()
        open_pool()
        try:
            first = await apply_migrations()
            second = await apply_migrations()
        finally:
            await close_pool()
        assert first == [path.name for path in migration_files()]
        assert second == []
    finally:
        await admin.execute(f'DROP DATABASE IF EXISTS "{scratch}" WITH (FORCE)')
        await admin.close()
