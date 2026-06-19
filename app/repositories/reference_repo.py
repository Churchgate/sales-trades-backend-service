from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.deal_reason import DealReason
from app.models.owner import Owner
from app.models.pipeline import Pipeline
from app.models.stage import Stage


async def upsert_pipeline(session: AsyncSession, data: dict[str, Any]) -> None:
    stmt = insert(Pipeline).values(**data)
    update_cols = {
        "name": stmt.excluded.name,
        "business_line": stmt.excluded.business_line,
        "is_default": stmt.excluded.is_default,
        "is_active": stmt.excluded.is_active,
    }
    stmt = stmt.on_conflict_do_update(index_elements=[Pipeline.id], set_=update_cols)
    await session.execute(stmt)


async def upsert_stage(session: AsyncSession, data: dict[str, Any]) -> None:
    stmt = insert(Stage).values(**data)
    update_cols = {
        "pipeline_id": stmt.excluded.pipeline_id,
        "name": stmt.excluded.name,
        "position": stmt.excluded.position,
        "forecast_type": stmt.excluded.forecast_type,
        "probability": stmt.excluded.probability,
    }
    stmt = stmt.on_conflict_do_update(index_elements=[Stage.id], set_=update_cols)
    await session.execute(stmt)


async def upsert_owner(session: AsyncSession, data: dict[str, Any]) -> None:
    stmt = insert(Owner).values(**data)
    update_cols = {
        "display_name": stmt.excluded.display_name,
        "email": stmt.excluded.email,
        "is_active": stmt.excluded.is_active,
    }
    stmt = stmt.on_conflict_do_update(index_elements=[Owner.id], set_=update_cols)
    await session.execute(stmt)


async def list_pipelines(session: AsyncSession, *, active_only: bool = False) -> list[Pipeline]:
    stmt = select(Pipeline)
    if active_only:
        stmt = stmt.where(Pipeline.is_active.is_(True))
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def list_stages(session: AsyncSession, pipeline_id: int | None = None) -> list[Stage]:
    stmt = select(Stage)
    if pipeline_id is not None:
        stmt = stmt.where(Stage.pipeline_id == pipeline_id)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def list_owners(session: AsyncSession, *, active_only: bool = False) -> list[Owner]:
    stmt = select(Owner)
    if active_only:
        stmt = stmt.where(Owner.is_active.is_(True))
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def upsert_deal_reason(session: AsyncSession, data: dict[str, Any]) -> None:
    stmt = insert(DealReason).values(**data)
    stmt = stmt.on_conflict_do_update(
        index_elements=[DealReason.id], set_={"name": stmt.excluded.name}
    )
    await session.execute(stmt)
