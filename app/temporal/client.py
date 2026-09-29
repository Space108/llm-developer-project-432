from temporalio.client import Client

from app.core.config import settings


async def connect() -> Client:
    return await Client.connect(settings.temporal_host)
