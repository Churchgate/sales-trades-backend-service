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


# --- Accountability / activity endpoints (B2b) ---

# Most-recent-activity timestamp for a deals_enriched row aliased `de` — the max of
# its last stage move, latest task, latest email, and latest stage/owner event.
_LAST_ACTIVITY = """greatest(
            de.stage_updated_at,
            (SELECT max(greatest(t.due_date, t.completed_date)) FROM tasks_snapshot t
             WHERE t.deal_id = de.deal_id),
            (SELECT max(e.conversation_time) FROM email_activity e WHERE e.deal_id = de.deal_id),
            (SELECT max(ev.occurred_at) FROM deal_events ev WHERE ev.deal_id = de.deal_id)
        )"""

_HAS_OPEN_TASK = (
    "EXISTS (SELECT 1 FROM tasks_snapshot t WHERE t.deal_id = de.deal_id AND t.status = 'open')"
)


def _open_where(
    filters: AnalyticsFilters, owner_scope: int | None
) -> tuple[str, dict[str, Any]]:
    """`build_filters` plus the open-deal restriction, for queries that alias
    `deals_enriched` as `de`."""
    where, params = build_filters(filters, owner_scope)
    open_clause = "de.forecast_type = 'Open'"
    where = f"{where} AND {open_clause}" if where else f" WHERE {open_clause}"
    return where, params


async def get_staleness(
    session: AsyncSession, filters: AnalyticsFilters, owner_scope: int | None
) -> list[dict[str, Any]]:
    """Per open deal: days since last stage move AND days since any activity, plus
    its stage and owner — the endpoint applies the `stale_days` threshold and rolls
    up by stage and by owner (spec §B2 staleness)."""
    where, params = _open_where(filters, owner_scope)
    sql = f"""
        SELECT
            de.deal_id,
            coalesce(de.stage_name, 'Unknown') AS stage_name,
            coalesce(o.display_name, 'Unassigned') AS owner_name,
            {_VALUE_EXPR} AS value,
            EXTRACT(EPOCH FROM (now() - de.stage_updated_at)) / 86400.0 AS days_since_stage_move,
            EXTRACT(EPOCH FROM (now() - {_LAST_ACTIVITY})) / 86400.0 AS days_since_activity
        FROM deals_enriched de
        LEFT JOIN owners o ON de.owner_id = o.id
        {where}
    """
    return await _rows(session, sql, params)


async def get_owner_accountability(
    session: AsyncSession, filters: AnalyticsFilters, owner_scope: int | None
) -> list[dict[str, Any]]:
    """Per-owner performance + accountability (spec §B2 owners): win counts/values,
    stale value, #no-next-action, #no-recent-activity, last CRM update, and
    #deals-progressed (a stage advance in the last `stale_days` from deal_events)."""
    where, params = build_filters(filters, owner_scope)
    params["stale_days"] = filters.stale_days
    sql = f"""
        WITH base AS (
            SELECT
                de.owner_id,
                coalesce(o.display_name, 'Unassigned') AS owner_name,
                de.forecast_type,
                de.stage_updated_at,
                {_VALUE_EXPR} AS value,
                EXTRACT(EPOCH FROM (now() - de.stage_updated_at)) / 86400.0
                    AS days_since_stage_move,
                EXTRACT(EPOCH FROM (now() - {_LAST_ACTIVITY})) / 86400.0 AS days_since_activity,
                {_HAS_OPEN_TASK} AS has_open_task,
                EXISTS (
                    SELECT 1 FROM deal_events ev
                    WHERE ev.deal_id = de.deal_id AND ev.event_type = 'stage_change'
                      AND ev.occurred_at > now() - make_interval(days => :stale_days)
                ) AS progressed_recently
            FROM deals_enriched de
            LEFT JOIN owners o ON de.owner_id = o.id
            {where}
        )
        SELECT
            owner_id,
            owner_name,
            count(*) AS total_deals,
            count(*) FILTER (WHERE forecast_type = 'Open') AS open_deals,
            count(*) FILTER (WHERE forecast_type = 'Closed Won') AS won_deals,
            count(*) FILTER (WHERE forecast_type = 'Closed Lost') AS lost_deals,
            coalesce(sum(value) FILTER (WHERE forecast_type = 'Open'), 0) AS open_value,
            coalesce(sum(value) FILTER (WHERE forecast_type = 'Closed Won'), 0) AS won_value,
            coalesce(sum(value) FILTER (
                WHERE forecast_type = 'Open' AND days_since_stage_move > :stale_days
            ), 0) AS stale_value,
            count(*) FILTER (WHERE forecast_type = 'Open' AND NOT has_open_task)
                AS no_next_action,
            count(*) FILTER (
                WHERE forecast_type = 'Open'
                  AND coalesce(days_since_activity, 1e9) > :stale_days
            ) AS no_recent_activity,
            count(*) FILTER (WHERE forecast_type = 'Open' AND progressed_recently)
                AS deals_progressed,
            max(stage_updated_at) AS last_crm_update
        FROM base
        GROUP BY owner_id, owner_name
        ORDER BY owner_name
    """
    return await _rows(session, sql, params)


async def get_next_actions(
    session: AsyncSession, filters: AnalyticsFilters, owner_scope: int | None
) -> dict[str, Any]:
    """Follow-up discipline over open deals: how many have a next task, a follow-up /
    expected-close date, and recent activity (spec §B2 next-actions)."""
    where, params = _open_where(filters, owner_scope)
    params["stale_days"] = filters.stale_days
    sql = f"""
        SELECT
            count(*) AS open_deals,
            count(*) FILTER (WHERE has_open_task) AS with_next_task,
            count(*) FILTER (WHERE has_follow_up_date) AS with_follow_up_date,
            count(*) FILTER (WHERE coalesce(days_since_activity, 1e9) <= :stale_days)
                AS with_recent_activity
        FROM (
            SELECT
                {_HAS_OPEN_TASK} AS has_open_task,
                (de.expected_close_date IS NOT NULL OR EXISTS (
                    SELECT 1 FROM tasks_snapshot t
                    WHERE t.deal_id = de.deal_id AND t.status = 'open' AND t.due_date IS NOT NULL
                )) AS has_follow_up_date,
                EXTRACT(EPOCH FROM (now() - {_LAST_ACTIVITY})) / 86400.0 AS days_since_activity
            FROM deals_enriched de
            {where}
        ) s
    """
    rows = await _rows(session, sql, params)
    return rows[0] if rows else {
        "open_deals": 0, "with_next_task": 0,
        "with_follow_up_date": 0, "with_recent_activity": 0,
    }


async def get_loss_reasons(
    session: AsyncSession, filters: AnalyticsFilters, owner_scope: int | None
) -> list[dict[str, Any]]:
    """Closed-Lost deals grouped by raw `lost_reason` (the endpoint maps these onto
    the audit's categories). Empty reason becomes '' so the endpoint can report the
    % lost with no reason recorded (spec §B2 loss-reasons)."""
    where, params = build_filters(filters, owner_scope)
    lost_clause = "forecast_type = 'Closed Lost'"
    where = f"{where} AND {lost_clause}" if where else f" WHERE {lost_clause}"
    sql = f"""
        SELECT
            coalesce(nullif(trim(lost_reason), ''), '') AS reason,
            count(*) AS lost_deals,
            coalesce(sum({_VALUE_EXPR}), 0) AS lost_value
        FROM deals_enriched
        {where}
        GROUP BY 1
    """
    return await _rows(session, sql, params)


async def get_ageing(
    session: AsyncSession, filters: AnalyticsFilters, owner_scope: int | None, *, group: str
) -> list[dict[str, Any]]:
    """Open-deal counts bucketed by `age_days` (0-30 / 30-90 / 90-365 / 365+),
    grouped by stage or by owner (spec §B2 ageing)."""
    where, params = _open_where(filters, owner_scope)
    key_expr = (
        "coalesce(o.display_name, 'Unassigned')"
        if group == "owner"
        else "coalesce(de.stage_name, 'Unknown')"
    )
    sql = f"""
        SELECT
            {key_expr} AS key,
            count(*) FILTER (WHERE de.age_days < 30) AS bucket_0_30,
            count(*) FILTER (WHERE de.age_days >= 30 AND de.age_days < 90) AS bucket_30_90,
            count(*) FILTER (WHERE de.age_days >= 90 AND de.age_days < 365) AS bucket_90_365,
            count(*) FILTER (WHERE de.age_days >= 365) AS bucket_365_plus
        FROM deals_enriched de
        LEFT JOIN owners o ON de.owner_id = o.id
        {where}
        GROUP BY 1
        ORDER BY 1
    """
    return await _rows(session, sql, params)


# Keyword → audit loss category. Heuristic substring match (lost_reason is free text);
# tune against real values. The first matching category wins; order matters.
_LOSS_CATEGORY_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("no budget", ("budget", "fund", "no money")),
    ("pricing", ("pric", "expensive", "cost", "afford", "too high")),
    ("competitor", ("competitor", "compet", "went with", "chose other", "lost to")),
    ("poor follow-up", ("follow", "no response", "unresponsive", "ghost", "no reply")),
    ("timing", ("timing", "postpone", "delay", "not ready", "next year", "on hold")),
    ("wrong target", ("not qualified", "wrong fit", "not a fit", "wrong target", "unqualified")),
    ("broker issue", ("broker", "agent")),
    (
        "product mismatch",
        ("product", "feature", "requirement", "spec", "size", "location", "floor"),
    ),
]


def categorize_loss_reason(reason: str | None) -> str:
    """Map a free-text `lost_reason` onto an audit category. Empty → 'no reason
    recorded'; unmatched non-empty → 'uncategorised'."""
    if not reason or not reason.strip():
        return "no reason recorded"
    low = reason.lower()
    for category, keywords in _LOSS_CATEGORY_KEYWORDS:
        if any(keyword in low for keyword in keywords):
            return category
    return "uncategorised"
