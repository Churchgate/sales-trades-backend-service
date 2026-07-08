"""GA4 website-analytics service — parsing + configured/unconfigured paths.

The GA client is mocked (no network); we assert the batch-report + realtime responses
are mapped onto the schema correctly and that missing env vars yield configured=false.
"""

from app.core.config import Settings
from app.services import web_analytics


class _Val:
    def __init__(self, value: str) -> None:
        self.value = value


class _Row:
    def __init__(self, dims: list[str], mets: list[str]) -> None:
        self.dimension_values = [_Val(x) for x in dims]
        self.metric_values = [_Val(x) for x in mets]


class _Report:
    def __init__(self, rows: list[_Row]) -> None:
        self.rows = rows


class _Resp:
    def __init__(self, reports: list[_Report]) -> None:
        self.reports = reports


class _FakeClient:
    def __init__(self) -> None:
        self._reports = [
            _Report([  # 0: summary (current + previous), 6 metrics each
                _Row(["date_range_0"], ["136", "157", "182", "71.4", "741", "5"]),
                _Row(["date_range_1"], ["10", "12", "14", "30", "50", "0"]),
            ]),
            _Report([_Row(["20260703"], ["38", "42"]), _Row(["20260704"], ["50", "55"])]),  # 1 cur
            _Report([_Row(["20260626"], ["5", "6"]), _Row(["20260627"], ["7", "8"])]),  # 2 prev
            _Report([_Row(["WTC Abuja | Home"], ["213"]), _Row(["Export"], ["20"])]),  # 3 pages
            _Report([_Row(["page_view"], ["182"]), _Row(["form_start"], ["3"])]),  # 4 events
            _Report([_Row(["Nigeria"], ["61"]), _Row(["Singapore"], ["41"])]),  # 5 countries
            _Report([_Row(["Direct"], ["133"]), _Row(["Organic Search"], ["56"])]),  # 6 channels
        ]
        self._i = 0

    def batch_run_reports(self, request):  # noqa: ANN001 - test double
        n = len(request.requests)  # GA caps at 5; service chunks, so serve in order
        chunk = self._reports[self._i : self._i + n]
        self._i += n
        return _Resp(chunk)

    def run_realtime_report(self, request):  # noqa: ANN001 - test double
        dim = request.dimensions[0].name
        if dim == "country":
            return _Report([_Row(["Nigeria"], ["2"]), _Row(["United Kingdom"], ["1"])])
        return _Report([_Row(["0"], ["1"]), _Row(["5"], ["2"])])  # minutesAgo


async def test_website_analytics_unconfigured_returns_flag() -> None:
    settings = Settings(ga_property_id="", ga_service_account_json="")
    result = await web_analytics.get_website_analytics(28, settings=settings)
    assert result.configured is False
    assert result.kpi is None


async def test_website_analytics_maps_report(monkeypatch) -> None:
    web_analytics._cache.clear()
    monkeypatch.setattr(web_analytics, "_get_client", lambda settings: _FakeClient())
    settings = Settings(ga_property_id="544089143", ga_service_account_json='{"x": 1}')

    result = await web_analytics.get_website_analytics(28, settings=settings)

    assert result.configured is True and result.error is None
    # KPI totals + previous window
    assert result.kpi.active_users == 136 and result.kpi.sessions == 157
    assert result.kpi.event_count == 741 and result.kpi.key_events == 5
    assert result.kpi.active_users_prev == 10 and result.kpi.event_count_prev == 50
    # daily series carries the previous-period overlay (aligned by index)
    assert [p.date for p in result.timeseries] == ["2026-07-03", "2026-07-04"]
    assert result.timeseries[0].active_users == 38 and result.timeseries[0].active_users_prev == 5
    assert result.timeseries[1].sessions == 55 and result.timeseries[1].sessions_prev == 8
    # top lists
    assert (result.top_pages[0].title, result.top_pages[0].views) == ("WTC Abuja | Home", 213)
    assert (result.events[0].name, result.events[0].count) == ("page_view", 182)
    assert (result.countries[0].country, result.countries[0].active_users) == ("Nigeria", 61)
    assert (result.channels[0].channel, result.channels[0].sessions) == ("Direct", 133)
    # realtime
    assert result.realtime.active_users == 3  # 2 + 1 across countries
    assert len(result.realtime.per_minute) == 30 and result.realtime.per_minute[29] == 1
    assert result.realtime.by_country[0].country == "Nigeria"
