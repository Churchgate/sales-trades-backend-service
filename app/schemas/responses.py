from datetime import datetime

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
