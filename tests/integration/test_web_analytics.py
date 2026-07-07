"""GA4 website-analytics service — parsing + configured/unconfigured paths.

The GA client is mocked (no network); we assert the batch-report response is mapped
onto the schema correctly and that missing env vars yield configured=false.
"""

from app.core.config import Settings
from app.services import web_analytics


class _Val:
    def __init__(self, value: str) -> None:
        self.value = value


class _Row:
    def __init__(self, dims: list[str], mets: list[str]) -> None:
        self.dimension_values = [_Val(d) for d in dims]
        self.metric_values = [_Val(m) for m in mets]


class _Report:
    def __init__(self, rows: list[_Row]) -> None:
        self.rows = rows


class _Resp:
    def __init__(self, reports: list[_Report]) -> None:
        self.reports = reports


class _FakeClient:
    def batch_run_reports(self, request):  # noqa: ANN001 - test double
        return _Resp([
            _Report([  # summary: current + previous date ranges
                _Row(["date_range_0"], ["136", "157", "182", "71.4"]),
                _Row(["date_range_1"], ["10", "12", "14", "30"]),
            ]),
            _Report([  # daily series
                _Row(["20260703"], ["38", "42"]),
                _Row(["20260704"], ["50", "55"]),
            ]),
            _Report([  # top pages
                _Row(["/"], ["123"]),
                _Row(["/export-launchpad/"], ["12"]),
            ]),
            _Report([  # events
                _Row(["page_view"], ["182"]),
                _Row(["form_start"], ["3"]),
            ]),
            _Report([  # countries
                _Row(["Nigeria"], ["120"]),
                _Row(["United Kingdom"], ["9"]),
            ]),
        ])


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
    assert result.kpi.active_users == 136 and result.kpi.sessions == 157
    assert result.kpi.page_views == 182 and result.kpi.avg_session_duration_s == 71.4
    assert result.kpi.active_users_prev == 10  # previous window
    assert [p.date for p in result.timeseries] == ["2026-07-03", "2026-07-04"]
    assert result.timeseries[1].sessions == 55
    assert (result.top_pages[0].path, result.top_pages[0].views) == ("/", 123)
    assert (result.events[0].name, result.events[0].count) == ("page_view", 182)
    assert (result.countries[0].country, result.countries[0].active_users) == ("Nigeria", 120)
