from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.deal import DealSnapshot

_UPSERTABLE_COLUMNS = [c.name for c in DealSnapshot.__table__.columns if c.name != "deal_id"]


async def get_deal(session: AsyncSession, deal_id: int) -> DealSnapshot | None:
    return await session.get(DealSnapshot, deal_id)


async def upsert_deal(session: AsyncSession, data: dict[str, Any]) -> None:
    """Insert or update a deals_snapshot row.

    `data` must include `deal_id`. Any of the other DealSnapshot columns present
    in `data` are written; columns not present are left untouched on conflict.
    """
    stmt = insert(DealSnapshot).values(**data)
    update_cols = {
        col: getattr(stmt.excluded, col)
        for col in _UPSERTABLE_COLUMNS
        if col in data
    }
    stmt = stmt.on_conflict_do_update(index_elements=[DealSnapshot.deal_id], set_=update_cols)
    await session.execute(stmt)


async def list_deals_for_pipeline(session: AsyncSession, pipeline_id: int) -> list[DealSnapshot]:
    stmt = select(DealSnapshot).where(DealSnapshot.pipeline_id == pipeline_id)
    result = await session.execute(stmt)
    return list(result.scalars().all())
