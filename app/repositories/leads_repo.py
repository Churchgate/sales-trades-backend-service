from sqlalchemy import Select, func, select
from sqlalchemy import delete as sa_delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.dml import Delete

from app.models.lead import CRM_SYNCED, PACK_SENT, Lead


async def get(session: AsyncSession, lead_id: int) -> Lead | None:
    return await session.get(Lead, lead_id)


async def get_by_campaign_email(
    session: AsyncSession, campaign_id: int, email: str
) -> Lead | None:
    """Lookup for dedup. `email` must already be normalised (lowercased)."""
    result = await session.execute(
        select(Lead).where(Lead.campaign_id == campaign_id, Lead.email == email)
    )
    return result.scalars().first()


async def create(session: AsyncSession, lead: Lead) -> Lead:
    session.add(lead)
    await session.commit()
    await session.refresh(lead)
    return lead


async def update(session: AsyncSession, lead: Lead) -> Lead:
    session.add(lead)
    await session.commit()
    await session.refresh(lead)
    return lead


async def delete(session: AsyncSession, lead: Lead) -> None:
    await session.delete(lead)
    await session.commit()


async def delete_for_campaign(
    session: AsyncSession,
    campaign_id: int,
    *,
    interest: str | None = None,
    inspection: bool | None = None,
    opt_in: bool | None = None,
    sync_status: str | None = None,
) -> int:
    """Bulk-delete leads matching the given filters (same shape as
    `list_for_campaign`). No filters = every lead in the campaign — the "purge
    a QA/test campaign" case. Returns the number of rows deleted."""
    stmt = _apply_filters(
        sa_delete(Lead),
        campaign_id=campaign_id,
        interest=interest,
        inspection=inspection,
        opt_in=opt_in,
        sync_status=sync_status,
    )
    result = await session.execute(stmt)
    await session.commit()
    return result.rowcount


def _apply_filters(
    stmt: Select | Delete,
    *,
    campaign_id: int,
    interest: str | None,
    inspection: bool | None,
    opt_in: bool | None,
    sync_status: str | None,
) -> Select | Delete:
    stmt = stmt.where(Lead.campaign_id == campaign_id)
    if interest is not None:
        stmt = stmt.where(Lead.interests.any(interest))
    if inspection is not None:
        stmt = stmt.where(Lead.inspection_requested.is_(inspection))
    if opt_in is not None:
        stmt = stmt.where(Lead.marketing_opt_in.is_(opt_in))
    if sync_status is not None:
        stmt = stmt.where(Lead.crm_sync_status == sync_status)
    return stmt


async def list_for_campaign(
    session: AsyncSession,
    campaign_id: int,
    *,
    interest: str | None = None,
    inspection: bool | None = None,
    opt_in: bool | None = None,
    sync_status: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[Lead]:
    stmt = _apply_filters(
        select(Lead),
        campaign_id=campaign_id,
        interest=interest,
        inspection=inspection,
        opt_in=opt_in,
        sync_status=sync_status,
    ).order_by(Lead.created_at.desc()).limit(limit).offset(offset)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def count_for_campaign(
    session: AsyncSession,
    campaign_id: int,
    *,
    interest: str | None = None,
    inspection: bool | None = None,
    opt_in: bool | None = None,
    sync_status: str | None = None,
) -> int:
    stmt = _apply_filters(
        select(func.count()).select_from(Lead),
        campaign_id=campaign_id,
        interest=interest,
        inspection=inspection,
        opt_in=opt_in,
        sync_status=sync_status,
    )
    return (await session.execute(stmt)).scalar_one()


async def list_pending_crm_sync(
    session: AsyncSession, *, statuses: list[str], limit: int = 200
) -> list[Lead]:
    """Leads awaiting CRM push (across all campaigns), oldest first."""
    result = await session.execute(
        select(Lead)
        .where(Lead.crm_sync_status.in_(statuses))
        .order_by(Lead.created_at)
        .limit(limit)
    )
    return list(result.scalars().all())


async def list_pending_pack_delivery(
    session: AsyncSession, *, statuses: list[str], limit: int = 200
) -> list[Lead]:
    """Leads awaiting digital-pack email (across all campaigns), oldest first."""
    result = await session.execute(
        select(Lead)
        .where(Lead.pack_delivery_status.in_(statuses))
        .order_by(Lead.created_at)
        .limit(limit)
    )
    return list(result.scalars().all())


# --- stats helpers ---


async def count_inspection_requests(session: AsyncSession, campaign_id: int) -> int:
    return await count_for_campaign(session, campaign_id, inspection=True)


async def count_opt_ins(session: AsyncSession, campaign_id: int) -> int:
    return await count_for_campaign(session, campaign_id, opt_in=True)


async def count_synced(session: AsyncSession, campaign_id: int) -> int:
    return await count_for_campaign(session, campaign_id, sync_status=CRM_SYNCED)


async def count_packs_delivered(session: AsyncSession, campaign_id: int) -> int:
    """Leads whose requested digital pack has been emailed."""
    result = await session.execute(
        select(func.count())
        .select_from(Lead)
        .where(Lead.campaign_id == campaign_id, Lead.pack_delivery_status == PACK_SENT)
    )
    return result.scalar_one()


async def counts_by_material(session: AsyncSession, campaign_id: int) -> dict[str, int]:
    """How many leads requested each material — the request-demand breakdown.

    Counts the verbatim `requested_materials` (what visitors actually asked for),
    so it includes any newsletter pseudo-item too; that's intentional for
    demand analysis of the form itself."""
    material = func.unnest(Lead.requested_materials).label("material")
    result = await session.execute(
        select(material, func.count().label("n"))
        .where(Lead.campaign_id == campaign_id)
        .group_by(material)
        .order_by(func.count().desc())
    )
    return {row.material: row.n for row in result.all()}


async def counts_by_interest(session: AsyncSession, campaign_id: int) -> dict[str, int]:
    interest = func.unnest(Lead.interests).label("interest")
    result = await session.execute(
        select(interest, func.count().label("n"))
        .where(Lead.campaign_id == campaign_id)
        .group_by(interest)
        .order_by(func.count().desc())
    )
    return {row.interest: row.n for row in result.all()}


async def counts_by_source(session: AsyncSession, campaign_id: int) -> dict[str, int]:
    result = await session.execute(
        select(Lead.source, func.count().label("n"))
        .where(Lead.campaign_id == campaign_id)
        .group_by(Lead.source)
        .order_by(func.count().desc())
    )
    return {row.source: row.n for row in result.all()}


async def counts_by_day(
    session: AsyncSession, campaign_id: int, tz: str
) -> list[tuple[object, int]]:
    """(local-date, count) per capture day, oldest first, in the given timezone."""
    day = func.date(func.timezone(tz, Lead.created_at)).label("day")
    result = await session.execute(
        select(day, func.count().label("n"))
        .where(Lead.campaign_id == campaign_id)
        .group_by(day)
        .order_by(day)
    )
    return [(row.day, row.n) for row in result.all()]
