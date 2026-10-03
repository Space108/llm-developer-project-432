from app.core.db import close_pool, open_pool
from app.core.migrate import apply_migrations, format_report, migration_files, split_sql


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


async def test_migrations_apply_on_an_empty_database_then_do_nothing(scratch_database) -> None:
    open_pool()
    try:
        first = await apply_migrations()
        second = await apply_migrations()
    finally:
        await close_pool()
    assert first == [path.name for path in migration_files()]
    assert second == []
