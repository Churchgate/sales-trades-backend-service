"""GA4 website-analytics reads for the dashboard "Website" panel.

Pulls visitors/sessions/pageviews, a daily time series, top pages, and top events
from the Google Analytics Data API (property `settings.ga_property_id`, authed with
`settings.ga_service_account_json`). The GA client is synchronous, so calls run in a
threadpool; results are cached briefly to stay well inside GA's quota.
"""

from __future__ import annotations

import json
import threading
import time

import anyio

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.schemas.web_analytics import (
    WebEventRow,
    WebKpi,
    WebPageRow,
    WebsiteAnalyticsResponse,
    WebTimePoint,
)

logger = get_logger(__name__)

_CACHE_TTL_S = 300
_cache: dict[int, tuple[float, WebsiteAnalyticsResponse]] = {}
_cache_lock = threading.Lock()
_client = None
_client_lock = threading.Lock()


def is_configured(settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    return bool(settings.ga_property_id and settings.ga_service_account_json)


def _get_client(settings: Settings):
    """Lazily build + cache the GA client from the service-account JSON."""
    global _client
    if _client is not None:
        return _client
    with _client_lock:
        if _client is None:
            from google.analytics.data_v1beta import BetaAnalyticsDataClient
            from google.oauth2 import service_account

            info = json.loads(settings.ga_service_account_json)
            creds = service_account.Credentials.from_service_account_info(info)
            _client = BetaAnalyticsDataClient(credentials=creds)
    return _client


def _iso(ga_date: str) -> str:
    """GA `date` dimension is YYYYMMDD → ISO YYYY-MM-DD."""
    return f"{ga_date[0:4]}-{ga_date[4:6]}-{ga_date[6:8]}" if len(ga_date) == 8 else ga_date


def _run_blocking(days: int, settings: Settings) -> WebsiteAnalyticsResponse:
    from google.analytics.data_v1beta.types import (
        BatchRunReportsRequest,
        DateRange,
        Dimension,
        Metric,
        OrderBy,
        RunReportRequest,
    )

    client = _get_client(settings)
    prop = f"properties/{settings.ga_property_id}"
    current = DateRange(start_date=f"{days}daysAgo", end_date="today")
    previous = DateRange(start_date=f"{2 * days}daysAgo", end_date=f"{days + 1}daysAgo")
    metric = lambda n: Metric(name=n)  # noqa: E731

    summary = RunReportRequest(
        property=prop,
        date_ranges=[current, previous],
        metrics=[metric("activeUsers"), metric("sessions"), metric("screenPageViews"),
                 metric("averageSessionDuration")],
    )
    series = RunReportRequest(
        property=prop, date_ranges=[current],
        dimensions=[Dimension(name="date")],
        metrics=[metric("activeUsers"), metric("sessions")],
        order_bys=[OrderBy(dimension=OrderBy.DimensionOrderBy(dimension_name="date"))],
    )
    pages = RunReportRequest(
        property=prop, date_ranges=[current],
        dimensions=[Dimension(name="pagePath")],
        metrics=[metric("screenPageViews")],
        order_bys=[OrderBy(metric=OrderBy.MetricOrderBy(metric_name="screenPageViews"), desc=True)],
        limit=10,
    )
    events = RunReportRequest(
        property=prop, date_ranges=[current],
        dimensions=[Dimension(name="eventName")],
        metrics=[metric("eventCount")],
        order_bys=[OrderBy(metric=OrderBy.MetricOrderBy(metric_name="eventCount"), desc=True)],
        limit=10,
    )
    resp = client.batch_run_reports(
        BatchRunReportsRequest(property=prop, requests=[summary, series, pages, events])
    )

    # -- summary: two date-range rows (GA adds a `dateRange` dimension) --
    def _range_vals(report, key: str) -> list[str]:
        for row in report.rows:
            if row.dimension_values and row.dimension_values[0].value == key:
                return [m.value for m in row.metric_values]
        return ["0", "0", "0", "0"]

    cur_v = _range_vals(resp.reports[0], "date_range_0")
    prev_v = _range_vals(resp.reports[0], "date_range_1")
    kpi = WebKpi(
        active_users=int(float(cur_v[0])), sessions=int(float(cur_v[1])),
        page_views=int(float(cur_v[2])), avg_session_duration_s=round(float(cur_v[3]), 1),
        active_users_prev=int(float(prev_v[0])), sessions_prev=int(float(prev_v[1])),
        page_views_prev=int(float(prev_v[2])),
        avg_session_duration_prev_s=round(float(prev_v[3]), 1),
    )

    timeseries = [
        WebTimePoint(
            date=_iso(row.dimension_values[0].value),
            active_users=int(float(row.metric_values[0].value)),
            sessions=int(float(row.metric_values[1].value)),
        )
        for row in resp.reports[1].rows
    ]
    top_pages = [
        WebPageRow(path=row.dimension_values[0].value, views=int(float(row.metric_values[0].value)))
        for row in resp.reports[2].rows
    ]
    event_rows = [
        WebEventRow(
            name=row.dimension_values[0].value,
            count=int(float(row.metric_values[0].value)),
        )
        for row in resp.reports[3].rows
    ]
    return WebsiteAnalyticsResponse(
        status_code=200, configured=True, days=days, kpi=kpi,
        timeseries=timeseries, top_pages=top_pages, events=event_rows,
    )


async def get_website_analytics(
    days: int = 28, settings: Settings | None = None
) -> WebsiteAnalyticsResponse:
    """Website analytics for the last `days` days (vs the preceding `days`). Never
    raises — returns configured=false when GA isn't set up, or error set on failure."""
    settings = settings or get_settings()
    if not is_configured(settings):
        return WebsiteAnalyticsResponse(status_code=200, configured=False, days=days)

    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(days)
        if hit and now - hit[0] < _CACHE_TTL_S:
            return hit[1]

    try:
        result = await anyio.to_thread.run_sync(_run_blocking, days, settings)
    except Exception as exc:  # noqa: BLE001 — surface a soft error, don't 500 the panel
        logger.exception("ga website analytics failed", days=days)
        return WebsiteAnalyticsResponse(
            status_code=200, configured=True, days=days, error=str(exc)[:300]
        )

    with _cache_lock:
        _cache[days] = (now, result)
    return result
