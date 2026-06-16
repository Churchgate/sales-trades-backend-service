from sqlalchemy.ext.asyncio import AsyncSession

from app.models.deal_event import DealEvent


async def insert_event(session: AsyncSession, event: DealEvent) -> DealEvent:
    session.add(event)
    await session.flush()
    return event
