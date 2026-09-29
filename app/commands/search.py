import argparse
import asyncio
import sys

from app.core.db import close_pool, open_pool
from app.core.logging import configure_logging
from app.core.migrate import apply_migrations
from app.repositories.search import SearchFilters, search_hybrid, search_vectors, search_words
from app.services.embeddings import embed_query


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("query")
    parser.add_argument("--mode", choices=("vector", "words", "hybrid"), default="hybrid")
    parser.add_argument("--document")
    parser.add_argument("--page", type=int)
    parser.add_argument("--section")
    parser.add_argument("--article")
    parser.add_argument("--brand")
    args = parser.parse_args()
    configure_logging()

    async def run() -> None:
        open_pool()
        try:
            await apply_migrations()
            filters = SearchFilters(
                document_id=args.document,
                page=args.page,
                section=args.section,
                article=args.article,
                brand=args.brand,
            )
            if args.mode == "words":
                hits = await search_words(args.query, filters)
            else:
                vector = await asyncio.to_thread(embed_query, args.query)
                if args.mode == "vector":
                    hits = await search_vectors(vector, filters)
                else:
                    hits = await search_hybrid(args.query, vector, filters)
            for hit in hits:
                print(
                    f"{hit.score:.4f}\t{hit.id}\t{hit.document_id}\t"
                    f"p={hit.page}\t{hit.section}\t{hit.article}\t{hit.brand}\t{hit.text}"
                )
        finally:
            await close_pool()

    asyncio.run(run())
    return 0


if __name__ == "__main__":
    sys.exit(main())
