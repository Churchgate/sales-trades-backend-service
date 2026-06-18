from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.deal import DealSnapshot
from app.models.stage import Stage

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


async def list_open_deal_ids(session: AsyncSession) -> list[int]:
    """Deal ids whose stage is still Open (spec §2: `forecast_type` is the reliable
    'is this deal active' signal, not stage-name matching). Activity syncs scope to
    these to stay within the Freshsales rate limit (spec §7)."""
    stmt = (
        select(DealSnapshot.deal_id)
        .join(Stage, DealSnapshot.stage_id == Stage.id)
        .where(Stage.forecast_type == "Open")
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())
