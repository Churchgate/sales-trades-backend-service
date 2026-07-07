"""Response schemas for the GA4 website-analytics panel (services/web_analytics.py).

Mirrors GA4's "report snapshot": KPI totals (vs the preceding window), a daily
series with a previous-period overlay, top pages / events / countries / channels,
and a realtime (last 30 min) block.
"""

from pydantic import BaseModel


class WebKpi(BaseModel):
    active_users: int
    sessions: int
    page_views: int
    avg_session_duration_s: float
    event_count: int
    key_events: int
    # Same metrics for the immediately preceding window (for % deltas).
    active_users_prev: int
    sessions_prev: int
    page_views_prev: int
    avg_session_duration_prev_s: float
    event_count_prev: int
    key_events_prev: int


class WebTimePoint(BaseModel):
    date: str  # ISO YYYY-MM-DD (current window)
    active_users: int
    sessions: int
    # Aligned value from the previous window (by day offset) for the GA overlay line.
    active_users_prev: int
    sessions_prev: int


class WebPageRow(BaseModel):
    title: str  # GA "Views by Page title"
    views: int


class WebEventRow(BaseModel):
    name: str
    count: int


class WebCountryRow(BaseModel):
    country: str
    active_users: int


class WebChannelRow(BaseModel):
    channel: str  # GA session default channel group (Direct, Organic Search, …)
    sessions: int


class WebRealtime(BaseModel):
    active_users: int  # active users in the last 30 minutes
    per_minute: list[int]  # 30 values, oldest → newest, for the sparkline
    by_country: list[WebCountryRow]


class WebsiteAnalyticsResponse(BaseModel):
    status_code: int
    configured: bool  # false when GA env vars are unset — UI shows a setup hint
    days: int
    error: str | None = None
    kpi: WebKpi | None = None
    timeseries: list[WebTimePoint] = []
    top_pages: list[WebPageRow] = []
    events: list[WebEventRow] = []
    countries: list[WebCountryRow] = []
    channels: list[WebChannelRow] = []
    realtime: WebRealtime | None = None
