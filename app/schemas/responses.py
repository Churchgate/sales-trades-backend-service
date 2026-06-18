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
