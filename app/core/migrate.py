import asyncio
import re
import sys
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.db import close_pool, connection, open_pool

MIGRATIONS = Path(__file__).resolve().parents[2] / "db" / "migrations"
# Ключ advisory-блокировки: два применяющих миграции процесса идут по очереди.
MIGRATION_LOCK_KEY = 432_000_001
_TABLE_READY = "to_regclass('schema_migrations') IS NOT NULL"


def split_sql(script: str) -> list[str]:
    """Делит скрипт по `;` вне строк, комментариев и dollar-quote."""
    statements: list[str] = []
    buf: list[str] = []
    i = 0
    n = len(script)
    dollar: str | None = None
    while i < n:
        if dollar is not None:
            if script.startswith(dollar, i):
                buf.append(dollar)
                i += len(dollar)
                dollar = None
                continue
            buf.append(script[i])
            i += 1
            continue
        if script.startswith("--", i):
            end = script.find("\n", i)
            if end == -1:
                break
            i = end + 1
            continue
        if script[i] == "'":
            buf.append(script[i])
            i += 1
            while i < n:
                buf.append(script[i])
                if script[i] == "'" and i + 1 < n and script[i + 1] == "'":
                    buf.append(script[i + 1])
                    i += 2
                    continue
                if script[i] == "'":
                    i += 1
                    break
                i += 1
            continue
        if script[i] == "$":
            match = re.match(r"\$[A-Za-z0-9_]*\$", script[i:])
            if match:
                dollar = match.group(0)
                buf.append(dollar)
                i += len(dollar)
                continue
        if script[i] == ";":
            statement = "".join(buf).strip()
            if statement:
                statements.append(statement)
            buf = []
            i += 1
            continue
        buf.append(script[i])
        i += 1
    tail = "".join(buf).strip()
    if tail:
        statements.append(tail)
    return statements


def format_report(applied: list[str]) -> str:
    if not applied:
        return "новых нет"
    return "\n".join(f"применена {name}" for name in applied)


def migration_files() -> list[Path]:
    return sorted(path for path in MIGRATIONS.glob("*.sql") if path.is_file())


async def _applied_versions(conn: AsyncConnection) -> set[str]:
    # Проверка без ошибки и отката: откат сбросил бы блокировку транзакции.
    ready = await conn.execute(text(f"SELECT {_TABLE_READY}"))
    if not ready.scalar():
        return set()
    result = await conn.execute(text("SELECT version FROM schema_migrations"))
    return {row[0] for row in result}


async def _execute_script(conn: AsyncConnection, script: str) -> None:
    for statement in split_sql(script):
        await conn.exec_driver_sql(statement)


def pending_migrations_sync(conn) -> list[str]:
    """Какие миграции ещё не применены. Только чтение: права на DDL не нужны."""
    with conn.cursor() as cur:
        cur.execute(f"SELECT {_TABLE_READY}")
        done: set[str] = set()
        if cur.fetchone()[0]:
            cur.execute("SELECT version FROM schema_migrations")
            done = {row[0] for row in cur.fetchall()}
    return [path.name for path in migration_files() if path.name not in done]


def apply_migrations_sync(conn) -> list[str]:
    """Те же миграции для синхронного соединения psycopg.

    Его открывает `with connection()` (путь каркаса Хекслета). Схему определяют только файлы
    `db/migrations`, второго описания таблиц в коде нет, поэтому порядок запуска не важен.
    """
    applied: list[str] = []
    with conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(%s)", (MIGRATION_LOCK_KEY,))
        cur.execute(f"SELECT {_TABLE_READY}")
        done: set[str] = set()
        if cur.fetchone()[0]:
            cur.execute("SELECT version FROM schema_migrations")
            done = {row[0] for row in cur.fetchall()}
        for path in migration_files():
            if path.name in done:
                continue
            for statement in split_sql(path.read_text(encoding="utf-8")):
                cur.execute(statement)
            cur.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (path.name,))
            applied.append(path.name)
    conn.commit()
    return applied


async def apply_migrations() -> list[str]:
    applied: list[str] = []
    async with connection() as conn:
        # Один применяющий за раз: процесс, воркер и тесты могут стартовать вместе.
        await conn.execute(
            text("SELECT pg_advisory_xact_lock(:key)"), {"key": MIGRATION_LOCK_KEY}
        )
        done = await _applied_versions(conn)
        for path in migration_files():
            if path.name in done:
                continue
            await _execute_script(conn, path.read_text(encoding="utf-8"))
            await conn.execute(
                text("INSERT INTO schema_migrations (version) VALUES (:version)"),
                {"version": path.name},
            )
            applied.append(path.name)
        await conn.commit()
    return applied


def main() -> int:
    async def run() -> None:
        open_pool()
        try:
            print(format_report(await apply_migrations()))
        finally:
            await close_pool()

    asyncio.run(run())
    return 0


if __name__ == "__main__":
    sys.exit(main())
