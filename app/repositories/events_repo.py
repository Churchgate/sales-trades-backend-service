from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.deal import DealSnapshot
from app.models.deal_event import DealEvent


async def insert_event(session: AsyncSession, event: DealEvent) -> DealEvent:
    session.add(event)
    await session.flush()
    return event


async def delete_events_by_source(session: AsyncSession, deal_id: int, source: str) -> None:
    """Drop a deal's events from one source (e.g. `timeline_backfill`) so the
    backfill can be re-run idempotently without touching webhook-sourced events."""
    await session.execute(
        delete(DealEvent).where(DealEvent.deal_id == deal_id, DealEvent.source == source)
    )


async def list_deal_ids_without_events(session: AsyncSession) -> list[int]:
    """Deal ids in deals_snapshot that have no deal_events history yet — the set the
    timeline backfill seeds (spec §6C)."""
    stmt = select(DealSnapshot.deal_id).where(
        DealSnapshot.deal_id.not_in(select(DealEvent.deal_id).distinct())
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())
