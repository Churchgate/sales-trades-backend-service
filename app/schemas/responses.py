from datetime import date, datetime

from pydantic import BaseModel

from app.schemas.auth import CurrentUser


class BaseResponse(BaseModel):
    status: str = "success"
    status_code: int


class AuthResponse(BaseResponse):
    """Login and refresh — tokens in body (for API clients) and in httpOnly cookies."""
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: CurrentUser


class MeResponse(BaseResponse):
    user: CurrentUser


class UserCreatedResponse(BaseResponse):
    user: CurrentUser


class UsersListResponse(BaseResponse):
    users: list[CurrentUser]


class MessageResponse(BaseResponse):
    message: str


class AnalyticsResponse(BaseResponse):
    """Base for analytics payloads. Every analytics response echoes `data_as_of` —
    the reporting watermark (most recent deal sync) — so each view can render a
    "data as of" banner (spec §B0)."""
    data_as_of: datetime | None = None


class DataFreshnessResponse(AnalyticsResponse):
    """Per-source last-run timestamps for the freshness banner (spec §B2).

    `email_last_activity_at` is the newest conversation on record rather than a sync
    time — `email_activity` has no sync-time column until the activity syncs land (B1).
    """
    deals_synced_at: datetime | None = None
    reference_synced_at: datetime | None = None
    tasks_synced_at: datetime | None = None
    email_last_activity_at: datetime | None = None


# --- Overview (per-business-line health) ---


class BusinessLineHealth(BaseModel):
    business_line: str
    total_deals: int
    open_deals: int
    open_value: float
    won_deals: int
    won_value: float
    lost_deals: int
    win_rate: float | None  # won / (won + lost); None when nothing has closed
    is_blended: bool = False  # the cross-line total — de-emphasise client-side


class OverviewResponse(AnalyticsResponse):
    business_lines: list[BusinessLineHealth]


# --- Pipeline funnel ---


class StageFunnelRow(BaseModel):
    business_line: str
    pipeline_id: int | None
    pipeline_name: str | None
    stage_id: int | None
    stage_name: str | None
    stage_position: int | None
    forecast_type: str | None
    deal_count: int
    total_value: float


class PipelineResponse(AnalyticsResponse):
    stages: list[StageFunnelRow]


# --- Active pipeline (true live pipeline) ---


class ExcludedDeal(BaseModel):
    deal_id: int
    reason: str


class ActivePipelineResponse(AnalyticsResponse):
    live_deals: int
    live_value: float
    excluded_count: int
    excluded: list[ExcludedDeal]


# --- Revenue forecast ("what is likely to close") ---


class RevenueMonthRow(BaseModel):
    business_line: str
    close_month: str | None  # 'YYYY-MM'; None for deals with no expected close date
    won_value: float
    open_value: float
    weighted_open_value: float  # open value weighted by stage probability


class RevenueResponse(AnalyticsResponse):
    months: list[RevenueMonthRow]


# --- Staleness (stale by stage AND owner) ---


class StalenessBucket(BaseModel):
    key: str  # stage name or owner name
    stale_deals: int
    stale_value: float


class StalenessResponse(AnalyticsResponse):
    stale_days: int
    definition: str  # echoes the staleness definition + denominator
    open_deals: int  # denominator
    stale_by_stage_move: int  # open deals with no stage move in > stale_days
    stale_value: float
    no_activity: int  # open deals with no activity of any kind in > stale_days
    by_stage: list[StalenessBucket]
    by_owner: list[StalenessBucket]


# --- Owner accountability ---


class OwnerAccountability(BaseModel):
    owner_id: int | None
    owner_name: str
    total_deals: int
    open_deals: int
    won_deals: int
    lost_deals: int
    win_rate: float | None
    open_value: float
    won_value: float
    stale_value: float  # open value with no stage move in > stale_days
    no_next_action: int  # open deals with no open task
    no_follow_up_date: int  # open deals with no expected-close/task due date
    overdue_tasks: int  # open tasks past due on this owner's open deals (spec §6E)
    no_recent_activity: int  # open deals with no activity in > stale_days
    deals_progressed: int  # open deals with a stage advance in the last stale_days
    last_crm_update: datetime | None


class OwnersResponse(AnalyticsResponse):
    stale_days: int
    owners: list[OwnerAccountability]


# --- Next-action / follow-up discipline ---


class NextActionsResponse(AnalyticsResponse):
    stale_days: int
    open_deals: int
    with_next_task: int
    with_next_task_pct: float
    with_follow_up_date: int
    with_follow_up_date_pct: float
    with_recent_activity: int
    with_recent_activity_pct: float
    overdue_tasks: int  # open tasks past due across the open deals (spec §6E)


# --- Loss reasons by category ---


class LossReasonCategory(BaseModel):
    category: str
    lost_deals: int
    lost_value: float


class LossReasonsResponse(AnalyticsResponse):
    total_lost: int
    no_reason_pct: float  # % of lost deals with no reason recorded
    categories: list[LossReasonCategory]


# --- Ageing by stage & owner ---


class AgeingBucketRow(BaseModel):
    key: str  # stage name or owner name
    bucket_0_30: int
    bucket_30_90: int
    bucket_90_365: int
    bucket_365_plus: int


class AgeingResponse(AnalyticsResponse):
    by_stage: list[AgeingBucketRow]
    by_owner: list[AgeingBucketRow]


# --- Lead source ---


class LeadSourceResponse(AnalyticsResponse):
    available: bool
    reason: str | None = None
    sources: list[dict[str, object]] = []


# --- Data integrity & leads gap (B4) ---


class DataQualityResponse(AnalyticsResponse):
    total_deals: int
    missing_owner: int
    missing_stage: int
    missing_pipeline: int
    missing_value: int
    duplicate_name_deals: int  # deals sharing a name with another deal
    notes: list[str]  # caveats about checks not yet possible (e.g. contacts)


class LeadsStatusResponse(AnalyticsResponse):
    leads_ingested: bool
    message: str


# --- Trend history (B3) ---


class TrendPoint(BaseModel):
    snapshot_date: date
    deal_count: int
    total_value: float


class StageTrendRow(BaseModel):
    pipeline_name: str | None
    stage_name: str | None
    stage_position: int | None
    current_deal_count: int
    current_value: float
    prev_deal_count: int
    prev_value: float
    value_delta: float  # current_value - prev_value


class TrendsResponse(AnalyticsResponse):
    current_date: date | None
    comparison_date: date | None  # the snapshot ~7 days before current (None if absent)
    series: list[TrendPoint]  # total pipeline value/count per snapshot date
    week_over_week: list[StageTrendRow]  # current vs comparison, by stage
