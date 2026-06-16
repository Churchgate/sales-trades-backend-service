from datetime import UTC, date, datetime

from app.freshsales.parsing import (
    PipelineStageResolver,
    parse_iso_date,
    parse_iso_timestamp,
    parse_webhook_date,
    parse_webhook_timestamp,
    split_custom_fields,
)
from app.models.pipeline import Pipeline
from app.models.stage import Stage


def test_parse_webhook_timestamp_converts_lagos_to_utc() -> None:
    expected = datetime(2026, 6, 15, 13, 30, tzinfo=UTC)
    assert parse_webhook_timestamp("06-15-2026 14:30:00") == expected


def test_parse_webhook_timestamp_date_only() -> None:
    assert parse_webhook_timestamp("06-15-2026") == datetime(2026, 6, 14, 23, 0, tzinfo=UTC)


def test_parse_webhook_timestamp_none() -> None:
    assert parse_webhook_timestamp(None) is None


def test_parse_webhook_date_has_no_timezone_shift() -> None:
    assert parse_webhook_date("06-15-2026") == date(2026, 6, 15)


def test_parse_iso_timestamp_handles_varying_offsets() -> None:
    expected = datetime(2026, 6, 15, 13, 30, tzinfo=UTC)
    assert parse_iso_timestamp("2026-06-15T14:30:00+01:00") == expected
    assert parse_iso_timestamp("2026-06-15T14:30:00Z") == datetime(2026, 6, 15, 14, 30, tzinfo=UTC)


def test_parse_iso_timestamp_none() -> None:
    assert parse_iso_timestamp(None) is None


def test_parse_iso_date() -> None:
    assert parse_iso_date("2026-06-15") == date(2026, 6, 15)
    assert parse_iso_date("2026-06-15T00:00:00+01:00") == date(2026, 6, 15)


def test_split_custom_fields_coerces_curated_types() -> None:
    curated, remaining = split_custom_fields(
        {
            "cf_project": "WTC Tower A",
            "cf_sqm_size": "120.5",
            "cf_term_end_date": "2027-01-01",
            "cf_other_field": "untouched",
        }
    )
    assert curated["cf_project"] == "WTC Tower A"
    assert curated["cf_sqm_size"] == 120.5
    assert curated["cf_term_end_date"] == date(2027, 1, 1)
    assert remaining == {"cf_other_field": "untouched"}


def test_split_custom_fields_blank_values_become_none() -> None:
    curated, _ = split_custom_fields({"cf_sqm_size": ""})
    assert curated["cf_sqm_size"] is None


def test_pipeline_stage_resolver_resolve_and_miss() -> None:
    pipelines = [Pipeline(id=1, name="New Rental Pipeline", business_line="WTC Abuja - Leasing")]
    stages = [
        Stage(id=10, pipeline_id=1, name="New", position=1, forecast_type="Open"),
        Stage(id=11, pipeline_id=1, name="Won", position=2, forecast_type="Closed Won"),
    ]
    resolver = PipelineStageResolver(pipelines, stages)

    resolution = resolver.resolve("New Rental Pipeline", "Won")
    assert resolution is not None
    assert resolution.pipeline_id == 1
    assert resolution.stage_id == 11

    assert resolver.resolve("New Rental Pipeline", "Nonexistent Stage") is None
    assert resolver.resolve("Unknown Pipeline", "Won") is None
