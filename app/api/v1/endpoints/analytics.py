from collections import defaultdict
from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.api.dependencies import OwnerScopeDep, SessionDep, get_current_user
from app.repositories import analytics_repo
from app.schemas.analytics import AnalyticsFilters
from app.schemas.responses import (
    ActivePipelineResponse,
    AgeingBucketRow,
    AgeingResponse,
    BusinessLineHealth,
    DataFreshnessResponse,
    DataQualityResponse,
    ExcludedDeal,
    LeadSourceResponse,
    LeadsStatusResponse,
    LossReasonCategory,
    LossReasonsResponse,
    NextActionsResponse,
    OverviewResponse,
    OwnerAccountability,
    OwnerResponseTime,
    OwnersResponse,
    PipelineResponse,
    RenewalDeal,
    RenewalsResponse,
    ResponseTimesResponse,
    RevenueMonthRow,
    RevenueResponse,
    StageFunnelRow,
    StageTrendRow,
    StalenessBucket,
    StalenessResponse,
    TrendPoint,
    TrendsResponse,
)
from app.schemas.web_analytics import WebsiteAnalyticsResponse
from app.services import web_analytics

# Authentication is shared across every analytics route at the router level; read
# endpoints take OwnerScopeDep so `rep` users are scoped to their own deals.
router = APIRouter(
    prefix="/analytics",
    tags=["analytics"],
    dependencies=[Depends(get_current_user)],
)

FiltersDep = Annotated[AnalyticsFilters, Query()]


def _win_rate(won: int, lost: int) -> float | None:
    closed = won + lost
    return won / closed if closed else None


def _pct(part: int, whole: int) -> float:
    return round(100 * part / whole, 1) if whole else 0.0


def _to_health(row: dict[str, object]) -> BusinessLineHealth:
    return BusinessLineHealth(
        business_line=row["business_line"],
        total_deals=row["total_deals"],
        open_deals=row["open_deals"],
        open_value=row["open_value"],
        won_deals=row["won_deals"],
        won_value=row["won_value"],
        lost_deals=row["lost_deals"],
        win_rate=_win_rate(row["won_deals"], row["lost_deals"]),
    )


@router.get("/website")
async def website_analytics(
    days: Annotated[int, Query(ge=1, le=365)] = 28,
) -> WebsiteAnalyticsResponse:
    """GA4 website traffic for the last `days` days vs the preceding window: KPI
    totals, a daily series, top pages, and top events. Reads the GA4 Data API (not
    the DB); returns configured=false when GA env vars are unset. Auth at router level."""
    return await web_analytics.get_website_analytics(days)


@router.get("/data-freshness")
async def data_freshness(session: SessionDep) -> DataFreshnessResponse:
    """Last-run timestamps per ingestion source — backs the "data as of" banner."""
    freshness = await analytics_repo.get_data_freshness(session)
    return DataFreshnessResponse(
        status_code=status.HTTP_200_OK,
        data_as_of=freshness.data_as_of,
        deals_synced_at=freshness.deals_synced_at,
        reference_synced_at=freshness.reference_synced_at,
        tasks_synced_at=freshness.tasks_synced_at,
        email_last_activity_at=freshness.email_last_activity_at,
    )


@router.get("/overview")
async def overview(
    session: SessionDep, owner_scope: OwnerScopeDep, filters: FiltersDep
) -> OverviewResponse:
    """Per-business-line health (win rate, open/won/lost). The final row is the
    cross-line blended total, flagged `is_blended` so the UI can de-emphasise it."""
    rows = await analytics_repo.get_overview(session, filters, owner_scope)
    lines = [_to_health(row) for row in rows]
    if lines:
        lines.append(
            BusinessLineHealth(
                business_line="All business lines",
                total_deals=sum(line.total_deals for line in lines),
                open_deals=sum(line.open_deals for line in lines),
                open_value=sum(line.open_value for line in lines),
                won_deals=sum(line.won_deals for line in lines),
                won_value=sum(line.won_value for line in lines),
                lost_deals=sum(line.lost_deals for line in lines),
                win_rate=_win_rate(
                    sum(line.won_deals for line in lines),
                    sum(line.lost_deals for line in lines),
                ),
                is_blended=True,
            )
        )
    return OverviewResponse(
        status_code=status.HTTP_200_OK,
        data_as_of=await analytics_repo.get_data_as_of(session),
        business_lines=lines,
    )


@router.get("/pipeline")
async def pipeline(
    session: SessionDep, owner_scope: OwnerScopeDep, filters: FiltersDep
) -> PipelineResponse:
    """Funnel: deal count + value by stage, ordered by stage position."""
    rows = await analytics_repo.get_pipeline_funnel(session, filters, owner_scope)
    return PipelineResponse(
        status_code=status.HTTP_200_OK,
        data_as_of=await analytics_repo.get_data_as_of(session),
        stages=[StageFunnelRow(**row) for row in rows],
    )


@router.get("/active-pipeline")
async def active_pipeline(
    session: SessionDep, owner_scope: OwnerScopeDep, filters: FiltersDep
) -> ActivePipelineResponse:
    """True live pipeline: open deals minus dormant / dead / duplicate, returning the
    live total and the excluded set with reasons."""
    rows = await analytics_repo.get_active_pipeline(session, filters, owner_scope)
    live = [row for row in rows if row["exclude_reason"] is None]
    excluded = [row for row in rows if row["exclude_reason"] is not None]
    return ActivePipelineResponse(
        status_code=status.HTTP_200_OK,
        data_as_of=await analytics_repo.get_data_as_of(session),
        live_deals=len(live),
        live_value=sum(row["value"] or 0 for row in live),
        excluded_count=len(excluded),
        excluded=[
            ExcludedDeal(deal_id=row["deal_id"], reason=row["exclude_reason"]) for row in excluded
        ],
    )


@router.get("/revenue")
async def revenue(
    session: SessionDep, owner_scope: OwnerScopeDep, filters: FiltersDep
) -> RevenueResponse:
    """What is likely to close: won + probability-weighted open value by close month."""
    rows = await analytics_repo.get_revenue_forecast(session, filters, owner_scope)
    return RevenueResponse(
        status_code=status.HTTP_200_OK,
        data_as_of=await analytics_repo.get_data_as_of(session),
        months=[RevenueMonthRow(**row) for row in rows],
    )


@router.get("/staleness")
async def staleness(
    session: SessionDep, owner_scope: OwnerScopeDep, filters: FiltersDep
) -> StalenessResponse:
    """Stale open deals by stage AND owner, surfacing both days-since-stage-move and
    days-since-activity, with the definition + denominator echoed."""
    rows = await analytics_repo.get_staleness(session, filters, owner_scope)
    threshold = filters.stale_days

    def _stale_move(row: dict[str, object]) -> bool:
        days = row["days_since_stage_move"]
        return days is not None and days > threshold

    def _no_activity(row: dict[str, object]) -> bool:
        days = row["days_since_activity"]
        return days is not None and days > threshold

    stale_rows = [row for row in rows if _stale_move(row)]
    by_stage: dict[str, list[float]] = defaultdict(lambda: [0, 0.0])
    by_owner: dict[str, list[float]] = defaultdict(lambda: [0, 0.0])
    for row in stale_rows:
        value = float(row["value"] or 0)
        by_stage[row["stage_name"]][0] += 1
        by_stage[row["stage_name"]][1] += value
        by_owner[row["owner_name"]][0] += 1
        by_owner[row["owner_name"]][1] += value

    def _buckets(grouped: dict[str, list[float]]) -> list[StalenessBucket]:
        return [
            StalenessBucket(key=key, stale_deals=int(agg[0]), stale_value=agg[1])
            for key, agg in sorted(grouped.items(), key=lambda kv: -kv[1][1])
        ]

    return StalenessResponse(
        status_code=status.HTTP_200_OK,
        data_as_of=await analytics_repo.get_data_as_of(session),
        stale_days=threshold,
        definition=(
            f"Open deals with no stage movement in more than {threshold} days. "
            f"Denominator: {len(rows)} open deals."
        ),
        open_deals=len(rows),
        stale_by_stage_move=len(stale_rows),
        stale_value=sum(float(row["value"] or 0) for row in stale_rows),
        no_activity=sum(1 for row in rows if _no_activity(row)),
        by_stage=_buckets(by_stage),
        by_owner=_buckets(by_owner),
    )


@router.get("/owners")
async def owners(
    session: SessionDep, owner_scope: OwnerScopeDep, filters: FiltersDep
) -> OwnersResponse:
    """Per-owner performance + accountability: win rate, open/won value, stale value,
    #no-next-action, #no-recent-activity, #deals-progressed, last CRM update."""
    rows = await analytics_repo.get_owner_accountability(session, filters, owner_scope)
    return OwnersResponse(
        status_code=status.HTTP_200_OK,
        data_as_of=await analytics_repo.get_data_as_of(session),
        stale_days=filters.stale_days,
        owners=[
            OwnerAccountability(
                owner_id=row["owner_id"],
                owner_name=row["owner_name"],
                total_deals=row["total_deals"],
                open_deals=row["open_deals"],
                won_deals=row["won_deals"],
                lost_deals=row["lost_deals"],
                win_rate=_win_rate(row["won_deals"], row["lost_deals"]),
                open_value=row["open_value"],
                won_value=row["won_value"],
                stale_value=row["stale_value"],
                no_next_action=row["no_next_action"],
                no_follow_up_date=row["no_follow_up_date"],
                overdue_tasks=row["overdue_tasks"],
                no_recent_activity=row["no_recent_activity"],
                deals_progressed=row["deals_progressed"],
                last_crm_update=row["last_crm_update"],
            )
            for row in rows
        ],
    )


@router.get("/next-actions")
async def next_actions(
    session: SessionDep, owner_scope: OwnerScopeDep, filters: FiltersDep
) -> NextActionsResponse:
    """Follow-up discipline: % of open deals with a next task, a follow-up/close date,
    and recent activity."""
    row = await analytics_repo.get_next_actions(session, filters, owner_scope)
    open_deals = row["open_deals"]
    return NextActionsResponse(
        status_code=status.HTTP_200_OK,
        data_as_of=await analytics_repo.get_data_as_of(session),
        stale_days=filters.stale_days,
        open_deals=open_deals,
        with_next_task=row["with_next_task"],
        with_next_task_pct=_pct(row["with_next_task"], open_deals),
        with_follow_up_date=row["with_follow_up_date"],
        with_follow_up_date_pct=_pct(row["with_follow_up_date"], open_deals),
        with_recent_activity=row["with_recent_activity"],
        with_recent_activity_pct=_pct(row["with_recent_activity"], open_deals),
        overdue_tasks=row["overdue_tasks"],
    )


@router.get("/loss-reasons")
async def loss_reasons(
    session: SessionDep, owner_scope: OwnerScopeDep, filters: FiltersDep
) -> LossReasonsResponse:
    """Lost deals mapped onto the audit's loss categories, with the % recorded with no
    reason and an 'uncategorised' bucket for unmapped reasons."""
    rows = await analytics_repo.get_loss_reasons(session, filters, owner_scope)
    total_lost = sum(row["lost_deals"] for row in rows)
    no_reason = sum(row["lost_deals"] for row in rows if not row["reason"])
    grouped: dict[str, list[float]] = defaultdict(lambda: [0, 0.0])
    for row in rows:
        category = analytics_repo.categorize_loss_reason(row["reason"] or None)
        grouped[category][0] += row["lost_deals"]
        grouped[category][1] += float(row["lost_value"] or 0)
    categories = [
        LossReasonCategory(category=key, lost_deals=int(agg[0]), lost_value=agg[1])
        for key, agg in sorted(grouped.items(), key=lambda kv: -kv[1][0])
    ]
    return LossReasonsResponse(
        status_code=status.HTTP_200_OK,
        data_as_of=await analytics_repo.get_data_as_of(session),
        total_lost=total_lost,
        no_reason_pct=_pct(no_reason, total_lost),
        categories=categories,
    )


@router.get("/ageing")
async def ageing(
    session: SessionDep, owner_scope: OwnerScopeDep, filters: FiltersDep
) -> AgeingResponse:
    """Open-deal age buckets (0-30 / 30-90 / 90-365 / 365+) by stage and by owner."""
    by_stage = await analytics_repo.get_ageing(session, filters, owner_scope, group="stage")
    by_owner = await analytics_repo.get_ageing(session, filters, owner_scope, group="owner")
    return AgeingResponse(
        status_code=status.HTTP_200_OK,
        data_as_of=await analytics_repo.get_data_as_of(session),
        by_stage=[AgeingBucketRow(**row) for row in by_stage],
        by_owner=[AgeingBucketRow(**row) for row in by_owner],
    )


@router.get("/lead-source")
async def lead_source(
    session: SessionDep, owner_scope: OwnerScopeDep, filters: FiltersDep
) -> LeadSourceResponse:
    """Channel performance — currently unavailable: no lead-source field is ingested
    on deals and the leads module is not yet synced (rather than guess, we say so)."""
    return LeadSourceResponse(
        status_code=status.HTTP_200_OK,
        data_as_of=await analytics_repo.get_data_as_of(session),
        available=False,
        reason=(
            "Lead source is not ingested: deals carry no lead-source field and the "
            "leads module is not yet synced (see /analytics/leads-status)."
        ),
        sources=[],
    )


@router.get("/data-quality")
async def data_quality(
    session: SessionDep, owner_scope: OwnerScopeDep, filters: FiltersDep
) -> DataQualityResponse:
    """Deal-integrity counts: missing owner/stage/pipeline/value and likely duplicates
    (deals sharing a name). Contact-level checks await contact ingestion."""
    row = await analytics_repo.get_data_quality(session, filters, owner_scope)
    return DataQualityResponse(
        status_code=status.HTTP_200_OK,
        data_as_of=await analytics_repo.get_data_as_of(session),
        total_deals=row["total_deals"],
        missing_owner=row["missing_owner"],
        missing_stage=row["missing_stage"],
        missing_pipeline=row["missing_pipeline"],
        missing_value=row["missing_value"],
        duplicate_name_deals=row["duplicate_name_deals"],
        notes=[
            "Contact-level checks (phone reachability, orphaned contacts) are "
            "unavailable — contacts are not yet ingested.",
            "Lead source is not a deal field; see /analytics/lead-source.",
        ],
    )


@router.get("/leads-status")
async def leads_status(session: SessionDep) -> LeadsStatusResponse:
    """Explicit flag that the top-of-funnel leads module is not yet ingested, so the
    funnel starts at deals — the UI should surface this gap prominently (spec §B4)."""
    return LeadsStatusResponse(
        status_code=status.HTTP_200_OK,
        data_as_of=await analytics_repo.get_data_as_of(session),
        leads_ingested=False,
        message=(
            "The leads module is not yet ingested. All analytics start at the deal "
            "stage; top-of-funnel lead volume and lead→deal conversion are not "
            "represented. Surface this as a prominent gap in the dashboard."
        ),
    )


@router.get("/renewals")
async def renewals(
    session: SessionDep,
    owner_scope: OwnerScopeDep,
    within_days: Annotated[int, Query(ge=1, le=1095)] = 90,
    business_line: str | None = None,
    pipeline_id: int | None = None,
    owner_id: int | None = None,
    stale_days: Annotated[int, Query(ge=1, le=365)] = 30,
    as_of: date | None = None,
) -> RenewalsResponse:
    """Lease renewal alerts: deals whose cf_term_end_date falls within `within_days`
    (plus a 30-day overdue grace), ordered most-urgent first (spec §3).

    Filters are declared as individual params rather than `FiltersDep` — FastAPI's
    Query-parameter-model exploding (`Annotated[AnalyticsFilters, Query()]`) breaks
    when a sibling `Query()` parameter (`within_days`) is present on the same route,
    always reporting `filters` itself as a missing required field. Verified via a
    minimal repro independent of this app; affects this endpoint only since it's the
    only one mixing `FiltersDep` with an extra Query param."""
    filters = AnalyticsFilters(
        business_line=business_line,
        pipeline_id=pipeline_id,
        owner_id=owner_id,
        stale_days=stale_days,
        as_of=as_of,
    )
    rows = await analytics_repo.get_renewals(session, filters, owner_scope, within_days)
    deals = [RenewalDeal(**row) for row in rows]
    overdue = sum(1 for deal in deals if deal.days_until_expiry < 0)
    return RenewalsResponse(
        status_code=status.HTTP_200_OK,
        data_as_of=await analytics_repo.get_data_as_of(session),
        within_days=within_days,
        upcoming_count=len(deals) - overdue,
        overdue_count=overdue,
        total_value=sum(deal.value or 0 for deal in deals),
        renewals=deals,
    )


@router.get("/trends")
async def trends(
    session: SessionDep, owner_scope: OwnerScopeDep, filters: FiltersDep
) -> TrendsResponse:
    """Week-over-week pipeline movement from pipeline_daily_snapshot: a daily total
    series plus current-vs-~7-days-ago value deltas by stage. Not owner-scoped (the
    daily rollup has no owner dimension)."""
    rows = await analytics_repo.get_trends_rows(session, filters)

    series_map: dict[object, list[float]] = defaultdict(lambda: [0, 0.0])
    for row in rows:
        series_map[row["snapshot_date"]][0] += row["deal_count"] or 0
        series_map[row["snapshot_date"]][1] += float(row["total_value"] or 0)
    dates = sorted(series_map)
    series = [
        TrendPoint(snapshot_date=d, deal_count=int(series_map[d][0]), total_value=series_map[d][1])
        for d in dates
    ]

    current_date = dates[-1] if dates else None
    comparison_date = (
        max((d for d in dates if d <= current_date - timedelta(days=7)), default=None)
        if current_date
        else None
    )

    current = {r["stage_id"]: r for r in rows if r["snapshot_date"] == current_date}
    previous = (
        {r["stage_id"]: r for r in rows if r["snapshot_date"] == comparison_date}
        if comparison_date
        else {}
    )
    week_over_week = []
    for stage_id, row in current.items():
        prev = previous.get(stage_id)
        current_value = float(row["total_value"] or 0)
        prev_value = float(prev["total_value"] or 0) if prev else 0.0
        week_over_week.append(
            StageTrendRow(
                pipeline_name=row["pipeline_name"],
                stage_name=row["stage_name"],
                stage_position=row["stage_position"],
                current_deal_count=row["deal_count"] or 0,
                current_value=current_value,
                prev_deal_count=(prev["deal_count"] or 0) if prev else 0,
                prev_value=prev_value,
                value_delta=current_value - prev_value,
            )
        )
    week_over_week.sort(key=lambda r: (r.pipeline_name or "", r.stage_position or 0))

    return TrendsResponse(
        status_code=status.HTTP_200_OK,
        data_as_of=await analytics_repo.get_data_as_of(session),
        current_date=current_date,
        comparison_date=comparison_date,
        series=series,
        week_over_week=week_over_week,
    )


@router.get("/response-times")
async def response_times(
    session: SessionDep, owner_scope: OwnerScopeDep, filters: FiltersDep
) -> ResponseTimesResponse:
    """Per-owner time-to-first-outreach (first outgoing email minus deal_created_at,
    spec §6D), plus how many deals have had no outreach at all."""
    rows = await analytics_repo.get_response_times(session, filters, owner_scope)
    owner_rows = [OwnerResponseTime(**row) for row in rows]
    # Overall mean weighted by deals with outreach (an owner's avg covers exactly its
    # outreach deals), so this is the true per-deal average first-response time.
    total = sum(o.deals_with_outreach for o in owner_rows)
    overall = (
        round(
            sum(
                o.avg_first_response_days * o.deals_with_outreach
                for o in owner_rows
                if o.avg_first_response_days is not None
            )
            / total,
            1,
        )
        if total
        else None
    )
    return ResponseTimesResponse(
        status_code=status.HTTP_200_OK,
        data_as_of=await analytics_repo.get_data_as_of(session),
        overall_avg_first_response_days=overall,
        owners=owner_rows,
    )
