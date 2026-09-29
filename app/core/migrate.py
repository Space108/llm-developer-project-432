import asyncio
import re
import sys
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.db import close_pool, connection, open_pool

MIGRATIONS = Path(__file__).resolve().parents[2] / "db" / "migrations"


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
    try:
        result = await conn.execute(text("SELECT version FROM schema_migrations"))
    except ProgrammingError:
        await conn.rollback()
        return set()
    return {row[0] for row in result}


async def _execute_script(conn: AsyncConnection, script: str) -> None:
    for statement in split_sql(script):
        await conn.exec_driver_sql(statement)


async def apply_migrations() -> list[str]:
    applied: list[str] = []
    async with connection() as conn:
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
