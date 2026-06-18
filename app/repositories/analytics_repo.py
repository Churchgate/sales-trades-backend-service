"""Read-only aggregation queries for the analytics API.

All aggregations read the `deals_enriched` view (migration ad205b8f1f36) so that
`business_line`, `stage_*` and `pipeline_name` come for free without re-joining.
Functions are plain async helpers over an `AsyncSession`, mirroring the other repos.
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def get_last_activity_at(session: AsyncSession, deal_id: int) -> datetime | None:
    """Most recent sign of life on a deal — the max across its last stage move, its
    latest task (due/completed), its latest email, and its latest stage/owner event
    (spec §B1). Computed in SQL, not stored. `greatest` ignores NULLs, so a deal with
    no activity in a given source still resolves to the others."""
    result = await session.execute(
        text(
            "SELECT greatest("
            "(SELECT stage_updated_at FROM deals_snapshot WHERE deal_id = :id), "
            "(SELECT max(greatest(due_date, completed_date)) FROM tasks_snapshot "
            " WHERE deal_id = :id), "
            "(SELECT max(conversation_time) FROM email_activity WHERE deal_id = :id), "
            "(SELECT max(occurred_at) FROM deal_events WHERE deal_id = :id))"
        ),
        {"id": deal_id},
    )
    return result.scalar_one_or_none()


async def get_data_as_of(session: AsyncSession) -> datetime | None:
    """The reporting watermark every analytics response echoes: the most recent deal
    sync time (`max(deals_snapshot.last_synced_at)`). `None` before the first sync."""
    result = await session.execute(text("SELECT max(last_synced_at) FROM deals_snapshot"))
    return result.scalar_one_or_none()


@dataclass(frozen=True)
class DataFreshness:
    data_as_of: datetime | None
    deals_synced_at: datetime | None
    reference_synced_at: datetime | None
    tasks_synced_at: datetime | None
    email_last_activity_at: datetime | None


async def get_data_freshness(session: AsyncSession) -> DataFreshness:
    """Per-source last-run timestamps for the "data as of" banner (spec §B2).

    Reference freshness is the newest of the pipelines/owners reference syncs.
    `email_activity` has no sync-time column yet (B1), so we surface the latest
    conversation we hold instead — named distinctly to avoid implying a sync time.
    """
    deals_synced_at = await get_data_as_of(session)
    reference_synced_at = (
        await session.execute(
            text(
                "SELECT greatest("
                "(SELECT max(updated_at) FROM pipelines), "
                "(SELECT max(updated_at) FROM owners))"
            )
        )
    ).scalar_one_or_none()
    tasks_synced_at = (
        await session.execute(text("SELECT max(last_synced_at) FROM tasks_snapshot"))
    ).scalar_one_or_none()
    email_last_activity_at = (
        await session.execute(text("SELECT max(conversation_time) FROM email_activity"))
    ).scalar_one_or_none()
    return DataFreshness(
        data_as_of=deals_synced_at,
        deals_synced_at=deals_synced_at,
        reference_synced_at=reference_synced_at,
        tasks_synced_at=tasks_synced_at,
        email_last_activity_at=email_last_activity_at,
    )
