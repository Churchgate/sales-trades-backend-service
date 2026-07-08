from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.email_event import EmailEvent


async def get_by_sg_event_id(session: AsyncSession, sg_event_id: str) -> EmailEvent | None:
    """Dedup lookup — SendGrid's webhook is at-least-once delivery."""
    result = await session.execute(
        select(EmailEvent).where(EmailEvent.sg_event_id == sg_event_id)
    )
    return result.scalars().first()


async def create(session: AsyncSession, event: EmailEvent) -> EmailEvent:
    session.add(event)
    await session.commit()
    await session.refresh(event)
    return event
