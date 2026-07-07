"""GA4 website-analytics reads for the dashboard "Website" panel (GA-snapshot style).

Pulls KPI totals (vs the preceding window), a daily series with a previous-period
overlay, top pages / events / countries / channels, and a realtime (last 30 min)
block from the Google Analytics Data API (property `settings.ga_property_id`, authed
with `settings.ga_service_account_json`). The GA client is synchronous, so calls run
in a threadpool; results are cached briefly to stay well inside GA's quota.
"""

from __future__ import annotations

import json
import threading
import time

import anyio

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.schemas.web_analytics import (
    WebChannelRow,
    WebCountryRow,
    WebEventRow,
    WebKpi,
    WebPageRow,
    WebRealtime,
    WebsiteAnalyticsResponse,
    WebTimePoint,
)

logger = get_logger(__name__)

_CACHE_TTL_S = 120
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


def _i(v: str) -> int:
    return int(float(v))


def _realtime(client, prop: str) -> WebRealtime | None:
    """Active users in the last 30 minutes: total, per-minute sparkline, by country.
    Best-effort — realtime failing must not sink the historical panel."""
    from google.analytics.data_v1beta.types import (
        Dimension,
        Metric,
        RunRealtimeReportRequest,
    )

    try:
        by_country_resp = client.run_realtime_report(
            RunRealtimeReportRequest(
                property=prop,
                dimensions=[Dimension(name="country")],
                metrics=[Metric(name="activeUsers")],
                limit=10,
            )
        )
        by_country = [
            WebCountryRow(
                country=r.dimension_values[0].value,
                active_users=_i(r.metric_values[0].value),
            )
            for r in by_country_resp.rows
        ]
        total = sum(c.active_users for c in by_country)

        minute_resp = client.run_realtime_report(
            RunRealtimeReportRequest(
                property=prop,
                dimensions=[Dimension(name="minutesAgo")],
                metrics=[Metric(name="activeUsers")],
            )
        )
        counts = [0] * 30
        for r in minute_resp.rows:
            idx = _i(r.dimension_values[0].value)
            if 0 <= idx < 30:
                counts[idx] = _i(r.metric_values[0].value)
        per_minute = [counts[29 - k] for k in range(30)]  # oldest → newest
        return WebRealtime(active_users=total, per_minute=per_minute, by_country=by_country)
    except Exception:  # noqa: BLE001
        logger.warning("ga realtime report failed", exc_info=True)
        return None


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
    m = lambda n: Metric(name=n)  # noqa: E731
    d = lambda n: Dimension(name=n)  # noqa: E731
    by_metric = lambda name: OrderBy(  # noqa: E731
        metric=OrderBy.MetricOrderBy(metric_name=name), desc=True
    )
    kpi_metrics = [
        m("activeUsers"), m("sessions"), m("screenPageViews"),
        m("averageSessionDuration"), m("eventCount"), m("keyEvents"),
    ]

    requests = [
        RunReportRequest(property=prop, date_ranges=[current, previous], metrics=kpi_metrics),
        RunReportRequest(
            property=prop, date_ranges=[current], dimensions=[d("date")],
            metrics=[m("activeUsers"), m("sessions")],
            order_bys=[OrderBy(dimension=OrderBy.DimensionOrderBy(dimension_name="date"))],
        ),
        RunReportRequest(
            property=prop, date_ranges=[previous], dimensions=[d("date")],
            metrics=[m("activeUsers"), m("sessions")],
            order_bys=[OrderBy(dimension=OrderBy.DimensionOrderBy(dimension_name="date"))],
        ),
        RunReportRequest(
            property=prop, date_ranges=[current], dimensions=[d("pageTitle")],
            metrics=[m("screenPageViews")], order_bys=[by_metric("screenPageViews")], limit=10,
        ),
        RunReportRequest(
            property=prop, date_ranges=[current], dimensions=[d("eventName")],
            metrics=[m("eventCount")], order_bys=[by_metric("eventCount")], limit=10,
        ),
        RunReportRequest(
            property=prop, date_ranges=[current], dimensions=[d("country")],
            metrics=[m("activeUsers")], order_bys=[by_metric("activeUsers")], limit=10,
        ),
        RunReportRequest(
            property=prop, date_ranges=[current], dimensions=[d("sessionDefaultChannelGroup")],
            metrics=[m("sessions")], order_bys=[by_metric("sessions")], limit=10,
        ),
    ]
    # GA caps batchRunReports at 5 requests per call — chunk and concatenate.
    r = []
    for i in range(0, len(requests), 5):
        resp = client.batch_run_reports(
            BatchRunReportsRequest(property=prop, requests=requests[i : i + 5])
        )
        r.extend(resp.reports)

    # -- summary: two date-range rows (GA adds a `dateRange` dimension) --
    def _range_vals(report, key: str) -> list[str]:
        for row in report.rows:
            if row.dimension_values and row.dimension_values[0].value == key:
                return [mv.value for mv in row.metric_values]
        return ["0"] * 6

    cur_v = _range_vals(r[0], "date_range_0")
    prev_v = _range_vals(r[0], "date_range_1")
    kpi = WebKpi(
        active_users=_i(cur_v[0]), sessions=_i(cur_v[1]), page_views=_i(cur_v[2]),
        avg_session_duration_s=round(float(cur_v[3]), 1),
        event_count=_i(cur_v[4]), key_events=_i(cur_v[5]),
        active_users_prev=_i(prev_v[0]), sessions_prev=_i(prev_v[1]), page_views_prev=_i(prev_v[2]),
        avg_session_duration_prev_s=round(float(prev_v[3]), 1),
        event_count_prev=_i(prev_v[4]), key_events_prev=_i(prev_v[5]),
    )

    # -- daily series with previous-period overlay (aligned by day offset) --
    cur_series = [(_iso(row.dimension_values[0].value),
                   _i(row.metric_values[0].value), _i(row.metric_values[1].value))
                  for row in r[1].rows]
    prev_series = [(_i(row.metric_values[0].value), _i(row.metric_values[1].value))
                   for row in r[2].rows]
    timeseries = []
    for idx, (dt, users, sess) in enumerate(cur_series):
        pu, ps = prev_series[idx] if idx < len(prev_series) else (0, 0)
        timeseries.append(WebTimePoint(
            date=dt, active_users=users, sessions=sess,
            active_users_prev=pu, sessions_prev=ps,
        ))

    top_pages = [
        WebPageRow(title=row.dimension_values[0].value, views=_i(row.metric_values[0].value))
        for row in r[3].rows
    ]
    events = [WebEventRow(name=row.dimension_values[0].value, count=_i(row.metric_values[0].value))
              for row in r[4].rows]
    countries = [WebCountryRow(country=row.dimension_values[0].value,
                               active_users=_i(row.metric_values[0].value))
                 for row in r[5].rows]
    channels = [WebChannelRow(channel=row.dimension_values[0].value,
                              sessions=_i(row.metric_values[0].value))
                for row in r[6].rows]

    return WebsiteAnalyticsResponse(
        status_code=200, configured=True, days=days, kpi=kpi,
        timeseries=timeseries, top_pages=top_pages, events=events,
        countries=countries, channels=channels, realtime=_realtime(client, prop),
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
