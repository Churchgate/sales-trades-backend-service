"""Read-only aggregation queries for the analytics API.

All aggregations read the `deals_enriched` view (migration ad205b8f1f36) so that
`business_line`, `stage_*` and `pipeline_name` come for free without re-joining.
Functions are plain async helpers over an `AsyncSession`, mirroring the other repos.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.analytics import AnalyticsFilters

# Deal value in the account's base currency, falling back to the deal-currency
# amount — the figure every value aggregation sums (avoids mixing currencies in
# blended totals, which the audit flagged).
_VALUE_EXPR = "coalesce(base_currency_amount, amount)"


def build_filters(filters: AnalyticsFilters, owner_scope: int | None) -> tuple[str, dict[str, Any]]:
    """Build a parameterised WHERE fragment over `deals_enriched` from the shared
    analytics filters. `owner_scope` (set for `rep` users) overrides any requested
    `owner_id` so a rep can never widen their view past their own deals."""
    clauses: list[str] = []
    params: dict[str, Any] = {}
    if filters.business_line is not None:
        clauses.append("business_line = :business_line")
        params["business_line"] = filters.business_line
    if filters.pipeline_id is not None:
        clauses.append("pipeline_id = :pipeline_id")
        params["pipeline_id"] = filters.pipeline_id
    effective_owner = owner_scope if owner_scope is not None else filters.owner_id
    if effective_owner is not None:
        clauses.append("owner_id = :owner_id")
        params["owner_id"] = effective_owner
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params


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


async def _rows(session: AsyncSession, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    result = await session.execute(text(sql), params)
    return [dict(row) for row in result.mappings().all()]


async def get_overview(
    session: AsyncSession, filters: AnalyticsFilters, owner_scope: int | None
) -> list[dict[str, Any]]:
    """Per-business-line health: open/won/lost counts and values (spec §B2 overview).
    Win rate and the blended total are derived in the endpoint."""
    where, params = build_filters(filters, owner_scope)
    sql = f"""
        SELECT
            coalesce(business_line, 'Unassigned') AS business_line,
            count(*) AS total_deals,
            count(*) FILTER (WHERE forecast_type = 'Open') AS open_deals,
            coalesce(sum({_VALUE_EXPR}) FILTER (WHERE forecast_type = 'Open'), 0) AS open_value,
            count(*) FILTER (WHERE forecast_type = 'Closed Won') AS won_deals,
            coalesce(sum({_VALUE_EXPR}) FILTER (WHERE forecast_type = 'Closed Won'), 0)
                AS won_value,
            count(*) FILTER (WHERE forecast_type = 'Closed Lost') AS lost_deals
        FROM deals_enriched
        {where}
        GROUP BY coalesce(business_line, 'Unassigned')
        ORDER BY business_line
    """
    return await _rows(session, sql, params)


async def get_pipeline_funnel(
    session: AsyncSession, filters: AnalyticsFilters, owner_scope: int | None
) -> list[dict[str, Any]]:
    """Funnel: deal count + value by stage, ordered by stage_position (spec §B2)."""
    where, params = build_filters(filters, owner_scope)
    sql = f"""
        SELECT
            coalesce(business_line, 'Unassigned') AS business_line,
            pipeline_id,
            pipeline_name,
            stage_id,
            stage_name,
            stage_position,
            forecast_type,
            count(*) AS deal_count,
            coalesce(sum({_VALUE_EXPR}), 0) AS total_value
        FROM deals_enriched
        {where}
        GROUP BY business_line, pipeline_id, pipeline_name,
                 stage_id, stage_name, stage_position, forecast_type
        ORDER BY business_line, pipeline_name, stage_position NULLS LAST
    """
    return await _rows(session, sql, params)


async def get_active_pipeline(
    session: AsyncSession, filters: AnalyticsFilters, owner_scope: int | None
) -> list[dict[str, Any]]:
    """Open deals tagged with an exclusion reason (dead/duplicate status, or dormant
    age > 730d) or NULL when live (spec §B2 active-pipeline). The endpoint splits the
    rows into the live total and the excluded set."""
    where, params = build_filters(filters, owner_scope)
    base = "forecast_type = 'Open'"
    where = f"{where} AND {base}" if where else f" WHERE {base}"
    sql = f"""
        SELECT
            deal_id,
            {_VALUE_EXPR} AS value,
            CASE
                WHEN cf_deal_status ILIKE '%dead%' OR cf_deal_status ILIKE '%duplicate%'
                    THEN 'dead/duplicate'
                WHEN age_days > 730 THEN 'dormant (age > 730d)'
                ELSE NULL
            END AS exclude_reason
        FROM deals_enriched
        {where}
    """
    return await _rows(session, sql, params)


async def get_revenue_forecast(
    session: AsyncSession, filters: AnalyticsFilters, owner_scope: int | None
) -> list[dict[str, Any]]:
    """"What is likely to close": won value + probability-weighted open value bucketed
    by expected-close month, per business line (spec §B2 revenue)."""
    where, params = build_filters(filters, owner_scope)
    sql = f"""
        SELECT
            coalesce(business_line, 'Unassigned') AS business_line,
            to_char(expected_close_date, 'YYYY-MM') AS close_month,
            coalesce(sum({_VALUE_EXPR}) FILTER (WHERE forecast_type = 'Closed Won'), 0)
                AS won_value,
            coalesce(sum({_VALUE_EXPR}) FILTER (WHERE forecast_type = 'Open'), 0)
                AS open_value,
            coalesce(sum(
                {_VALUE_EXPR} * coalesce(stage_probability, 0) / 100.0
            ) FILTER (WHERE forecast_type = 'Open'), 0) AS weighted_open_value
        FROM deals_enriched
        {where}
        GROUP BY business_line, to_char(expected_close_date, 'YYYY-MM')
        ORDER BY business_line, close_month NULLS LAST
    """
    return await _rows(session, sql, params)
