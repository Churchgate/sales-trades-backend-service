from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.trade_lead import CRM_SYNCED, TradeLead
from app.models.trade_program import TradeProgram

# --- programs ---


async def get_program(session: AsyncSession, program_id: int) -> TradeProgram | None:
    return await session.get(TradeProgram, program_id)


async def get_program_by_slug(session: AsyncSession, slug: str) -> TradeProgram | None:
    result = await session.execute(select(TradeProgram).where(TradeProgram.slug == slug))
    return result.scalars().first()


async def list_programs(session: AsyncSession, *, status: str | None = None) -> list[TradeProgram]:
    stmt = select(TradeProgram)
    if status is not None:
        stmt = stmt.where(TradeProgram.status == status)
    result = await session.execute(stmt.order_by(TradeProgram.created_at.desc()))
    return list(result.scalars().all())


async def create_program(session: AsyncSession, program: TradeProgram) -> TradeProgram:
    session.add(program)
    await session.commit()
    await session.refresh(program)
    return program


async def update_program(session: AsyncSession, program: TradeProgram) -> TradeProgram:
    session.add(program)
    await session.commit()
    await session.refresh(program)
    return program


# --- leads (participants) ---


async def get_lead(session: AsyncSession, lead_id: int) -> TradeLead | None:
    return await session.get(TradeLead, lead_id)


async def get_by_program_email(
    session: AsyncSession, program_id: int, email: str
) -> TradeLead | None:
    """Lookup for dedup. `email` must already be normalised (lowercased) and
    non-empty — the partial unique index only covers non-empty emails, so an
    empty-email 2nd participant is never deduped against another."""
    if not email:
        return None
    result = await session.execute(
        select(TradeLead).where(
            TradeLead.trade_program_id == program_id, TradeLead.email == email
        )
    )
    return result.scalars().first()


async def list_by_registration(session: AsyncSession, registration_id: str) -> list[TradeLead]:
    result = await session.execute(
        select(TradeLead)
        .where(TradeLead.registration_id == registration_id)
        .order_by(TradeLead.participant_index)
    )
    return list(result.scalars().all())


async def create_lead(session: AsyncSession, lead: TradeLead) -> TradeLead:
    session.add(lead)
    await session.commit()
    await session.refresh(lead)
    return lead


async def update_lead(session: AsyncSession, lead: TradeLead) -> TradeLead:
    session.add(lead)
    await session.commit()
    await session.refresh(lead)
    return lead


def _apply_filters(
    stmt: Select,
    *,
    program_id: int | None = None,
    crm_sync_status: str | None = None,
    eligibility_status: str | None = None,
    search: str | None = None,
) -> Select:
    if program_id is not None:
        stmt = stmt.where(TradeLead.trade_program_id == program_id)
    if crm_sync_status is not None:
        stmt = stmt.where(TradeLead.crm_sync_status == crm_sync_status)
    if eligibility_status is not None:
        stmt = stmt.where(TradeLead.eligibility_status == eligibility_status)
    if search:
        pattern = f"%{search.lower()}%"
        stmt = stmt.where(
            func.lower(TradeLead.first_name + " " + TradeLead.last_name).like(pattern)
            | func.lower(TradeLead.email).like(pattern)
            | func.lower(func.coalesce(TradeLead.company, "")).like(pattern)
        )
    return stmt


async def list_leads(
    session: AsyncSession,
    program_id: int,
    *,
    crm_sync_status: str | None = None,
    eligibility_status: str | None = None,
    search: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[TradeLead]:
    stmt = _apply_filters(
        select(TradeLead),
        program_id=program_id,
        crm_sync_status=crm_sync_status,
        eligibility_status=eligibility_status,
        search=search,
    ).order_by(TradeLead.created_at.desc()).limit(limit).offset(offset)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def count_leads(
    session: AsyncSession,
    program_id: int,
    *,
    crm_sync_status: str | None = None,
    eligibility_status: str | None = None,
    search: str | None = None,
) -> int:
    stmt = _apply_filters(
        select(func.count()).select_from(TradeLead),
        program_id=program_id,
        crm_sync_status=crm_sync_status,
        eligibility_status=eligibility_status,
        search=search,
    )
    return (await session.execute(stmt)).scalar_one()


async def list_pending_crm_sync(
    session: AsyncSession, *, statuses: list[str], limit: int = 200
) -> list[TradeLead]:
    """Trade participants awaiting CRM push, oldest first."""
    result = await session.execute(
        select(TradeLead)
        .where(TradeLead.crm_sync_status.in_(statuses))
        .order_by(TradeLead.created_at)
        .limit(limit)
    )
    return list(result.scalars().all())


async def program_stats(session: AsyncSession, program_id: int) -> dict:
    """Registration/participant counts + status breakdowns for the program
    detail page. Registrations and participants are counted distinctly since
    a registration may have one or two participant rows."""
    total_participants = await count_leads(session, program_id)

    registrations = await session.execute(
        select(func.count(func.distinct(TradeLead.registration_id))).where(
            TradeLead.trade_program_id == program_id
        )
    )
    total_registrations = registrations.scalar_one()

    crm_rows = await session.execute(
        select(TradeLead.crm_sync_status, func.count())
        .where(TradeLead.trade_program_id == program_id)
        .group_by(TradeLead.crm_sync_status)
    )
    crm_breakdown = {row[0]: row[1] for row in crm_rows.all()}

    eligibility_rows = await session.execute(
        select(TradeLead.eligibility_status, func.count())
        .where(TradeLead.trade_program_id == program_id)
        .group_by(TradeLead.eligibility_status)
    )
    eligibility_breakdown = {row[0]: row[1] for row in eligibility_rows.all()}

    return {
        "total_registrations": total_registrations,
        "total_participants": total_participants,
        "crm_sync_breakdown": crm_breakdown,
        "eligibility_breakdown": eligibility_breakdown,
    }


async def count_synced(session: AsyncSession, program_id: int) -> int:
    return await count_leads(session, program_id, crm_sync_status=CRM_SYNCED)
