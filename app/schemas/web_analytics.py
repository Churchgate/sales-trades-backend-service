"""Response schemas for the GA4 website-analytics panel (services/web_analytics.py)."""

from pydantic import BaseModel


class WebKpi(BaseModel):
    active_users: int
    sessions: int
    page_views: int
    avg_session_duration_s: float
    # Same metrics for the immediately preceding window (for % deltas in the UI).
    active_users_prev: int
    sessions_prev: int
    page_views_prev: int
    avg_session_duration_prev_s: float


class WebTimePoint(BaseModel):
    date: str  # ISO YYYY-MM-DD
    active_users: int
    sessions: int


class WebPageRow(BaseModel):
    path: str
    views: int


class WebEventRow(BaseModel):
    name: str
    count: int


class WebsiteAnalyticsResponse(BaseModel):
    status_code: int
    configured: bool  # false when GA env vars are unset — UI shows a setup hint
    days: int
    error: str | None = None
    kpi: WebKpi | None = None
    timeseries: list[WebTimePoint] = []
    top_pages: list[WebPageRow] = []
    events: list[WebEventRow] = []
