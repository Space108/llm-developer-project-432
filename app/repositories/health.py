from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def vector_extension_active(session: AsyncSession) -> bool:
    """База отвечает и расширение векторного поиска включено. Ошибки базы не глушатся."""
    await session.execute(text("SELECT 1"))
    result = await session.execute(
        text("SELECT extname FROM pg_extension WHERE extname = 'vector'")
    )
    return result.first() is not None
