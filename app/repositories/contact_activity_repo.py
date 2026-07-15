"""Persistence + aggregation for `contact_activity` (NOG per-contact outreach).

Writes are idempotent (deduped by `source_key`); reads power the NOG Activities page:
a per-salesperson summary and a drill-down list, both filterable by date range,
owner, and prospect tier.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.contact_activity import ContactActivity

# Grouping label for contacts with no assigned owner (cf_owner is null).
UNASSIGNED = "Unassigned"


async def upsert(session: AsyncSession, data: dict[str, Any]) -> None:
    """Insert a `contact_activity`, or refresh it if `source_key` already exists (owner
    / tier / timestamp can change between syncs). Caller commits."""
    existing = (
        await session.execute(
            select(ContactActivity).where(ContactActivity.source_key == data["source_key"])
        )
    ).scalar_one_or_none()
    if existing is None:
        session.add(ContactActivity(**data))
        return
    for key, value in data.items():
        setattr(existing, key, value)
    session.add(existing)


def _scoped(campaign_id: int, start: datetime, end: datetime, owner: str | None, tier: str | None):
    """Common WHERE clause: campaign + [start,end] + optional owner/tier filters.
    `owner="Unassigned"` selects rows with a null owner_name."""
    conds = [
        ContactActivity.campaign_id == campaign_id,
        ContactActivity.occurred_at >= start,
        ContactActivity.occurred_at <= end,
    ]
    if owner == UNASSIGNED:
        conds.append(ContactActivity.owner_name.is_(None))
    elif owner:
        conds.append(ContactActivity.owner_name == owner)
    if tier:
        conds.append(ContactActivity.prospect_tier == tier)
    return conds


async def summary_by_owner(
    session: AsyncSession,
    campaign_id: int,
    start: datetime,
    end: datetime,
    *,
    owner: str | None = None,
    tier: str | None = None,
) -> list[tuple[str, str, int]]:
    """(owner_name, activity_type, count) grouped rows for the summary table.
    Null owner_name is reported as "Unassigned"."""
    owner_label = func.coalesce(ContactActivity.owner_name, UNASSIGNED)
    result = await session.execute(
        select(owner_label, ContactActivity.activity_type, func.count().label("n"))
        .where(*_scoped(campaign_id, start, end, owner, tier))
        .group_by(owner_label, ContactActivity.activity_type)
    )
    return [(row[0], row[1], row[2]) for row in result.all()]


async def list_activities(
    session: AsyncSession,
    campaign_id: int,
    start: datetime,
    end: datetime,
    *,
    owner: str | None = None,
    tier: str | None = None,
    limit: int = 500,
) -> list[ContactActivity]:
    """Individual activities (newest first) for the drill-down, capped at `limit`."""
    result = await session.execute(
        select(ContactActivity)
        .where(*_scoped(campaign_id, start, end, owner, tier))
        .order_by(ContactActivity.occurred_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def distinct_owners(session: AsyncSession, campaign_id: int) -> list[str]:
    """Distinct owner names seen in this campaign's activity (for the filter dropdown);
    null owners surface as "Unassigned"."""
    owner_label = func.coalesce(ContactActivity.owner_name, UNASSIGNED)
    result = await session.execute(
        select(owner_label).where(ContactActivity.campaign_id == campaign_id).distinct()
    )
    return sorted({row[0] for row in result.all()})
