import asyncio
import sys

from app.core.db import close_pool, open_pool
from app.core.logging import configure_logging
from app.core.migrate import apply_migrations
from app.services.index import index_missing


def main() -> int:
    configure_logging()

    async def run() -> None:
        open_pool()
        try:
            await apply_migrations()
            await index_missing()
        finally:
            await close_pool()

    asyncio.run(run())
    return 0


if __name__ == "__main__":
    sys.exit(main())
