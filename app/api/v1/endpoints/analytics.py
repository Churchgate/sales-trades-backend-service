from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.api.dependencies import OwnerScopeDep, SessionDep, get_current_user
from app.repositories import analytics_repo
from app.schemas.analytics import AnalyticsFilters
from app.schemas.responses import (
    ActivePipelineResponse,
    BusinessLineHealth,
    DataFreshnessResponse,
    ExcludedDeal,
    OverviewResponse,
    PipelineResponse,
    RevenueMonthRow,
    RevenueResponse,
    StageFunnelRow,
)

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
