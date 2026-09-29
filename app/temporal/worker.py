import asyncio
from concurrent.futures import ThreadPoolExecutor

from temporalio.worker import Worker

from app.core.config import settings
from app.core.db import close_pool, open_pool
from app.temporal.activities import (
    critique_activity,
    critique_context_activity,
    empty_context_card_activity,
    extract_activity,
    filter_output_activity,
    generate_activity,
    generate_context_activity,
    index_document_activity,
    parse_document_activity,
    prepare_document_activity,
    search_context_activity,
    set_status_activity,
    verify_citations_activity,
)
from app.temporal.client import connect
from app.temporal.workflows import CardWorkflow, DocumentWorkflow


async def main() -> None:
    open_pool()
    from app.core.db import remember_loop

    remember_loop(asyncio.get_running_loop())
    client = await connect()
    try:
        worker = Worker(
            client,
            task_queue=settings.temporal_task_queue,
            workflows=[CardWorkflow, DocumentWorkflow],
            activities=[
                extract_activity,
                generate_activity,
                critique_activity,
                set_status_activity,
                parse_document_activity,
                prepare_document_activity,
                index_document_activity,
                search_context_activity,
                empty_context_card_activity,
                generate_context_activity,
                verify_citations_activity,
                critique_context_activity,
                filter_output_activity,
            ],
            activity_executor=ThreadPoolExecutor(max_workers=8),
        )
        await worker.run()
    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
