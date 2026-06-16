from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.core.config import get_settings
from app.models.pipeline import Pipeline
from app.models.stage import Stage

# Curated cf_* fields promoted to typed columns on deals_snapshot (spec §3).
CURATED_CF_FIELDS: set[str] = {
    "cf_project",
    "cf_floor",
    "cf_sqm_size",
    "cf_product_category",
    "cf_term",
    "cf_start_date",
    "cf_term_end_date",
    "cf_deal_status",
    "cf_total_lease_amount",
}

_NUMERIC_CF_FIELDS: set[str] = {"cf_sqm_size", "cf_term", "cf_total_lease_amount"}
_DATE_CF_FIELDS: set[str] = {"cf_start_date", "cf_term_end_date"}

_WEBHOOK_TIMESTAMP_FORMATS = ("%m-%d-%Y %H:%M:%S", "%m-%d-%Y")


def parse_webhook_timestamp(value: str | None, tz_name: str | None = None) -> datetime | None:
    """Parse a Freshsales webhook timestamp (`MM-DD-YYYY [HH:MM:SS]`, no TZ) to UTC.

    Webhook timestamps carry no explicit timezone; interpret them in the
    account's configured timezone (FRESHSALES_TZ, spec §7 open question #4).
    """
    if not value:
        return None
    value = value.strip()
    tz = ZoneInfo(tz_name or get_settings().freshsales_tz)
    for fmt in _WEBHOOK_TIMESTAMP_FORMATS:
        try:
            naive = datetime.strptime(value, fmt)
        except ValueError:
            continue
        return naive.replace(tzinfo=tz).astimezone(UTC)
    raise ValueError(f"Unrecognized webhook timestamp format: {value!r}")


def parse_webhook_date(value: str | None) -> date | None:
    """Parse a Freshsales webhook date (`MM-DD-YYYY`, date-only) with no timezone shift.

    Unlike `parse_webhook_timestamp`, this is for pure-date fields (e.g.
    `expected_close_date`) where converting to UTC could shift the calendar date.
    """
    if not value:
        return None
    date_part = value.strip().split(" ")[0]
    return datetime.strptime(date_part, "%m-%d-%Y").date()


def parse_iso_timestamp(value: str | None) -> datetime | None:
    """Parse a REST API ISO-8601 timestamp (varying offsets: +01:00, +00:00, Z) to UTC."""
    if not value:
        return None
    return datetime.fromisoformat(value).astimezone(UTC)


def parse_iso_date(value: str | None) -> date | None:
    """Parse a REST API date-only field (e.g. `expected_close`), no timezone shift."""
    if not value:
        return None
    return date.fromisoformat(value[:10])


def split_custom_fields(cf: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Split a `cf_*` dict into (curated typed columns, remaining custom_fields JSONB)."""
    curated: dict[str, Any] = {}
    remaining: dict[str, Any] = {}
    for key, value in cf.items():
        if key in CURATED_CF_FIELDS:
            curated[key] = _coerce_cf_value(key, value)
        else:
            remaining[key] = value
    return curated, remaining


def _coerce_cf_value(key: str, value: Any) -> Any:
    if value is None or value == "":
        return None
    if key in _NUMERIC_CF_FIELDS:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    if key in _DATE_CF_FIELDS:
        return _coerce_date(value)
    return value


def _coerce_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            pass
        try:
            return datetime.strptime(value, "%m-%d-%Y").date()
        except ValueError:
            return None
    return None


@dataclass(frozen=True)
class StageResolution:
    pipeline_id: int
    stage_id: int


class PipelineStageResolver:
    """In-memory `(pipeline_name, stage_name) -> (pipeline_id, stage_id)` resolver.

    Webhooks identify pipeline/stage by name only (spec §7); this resolver is built
    from the `pipelines`/`stages` reference tables and refreshed alongside them.
    """

    def __init__(self, pipelines: list[Pipeline], stages: list[Stage]) -> None:
        pipeline_by_id = {p.id: p for p in pipelines}
        self._lookup: dict[tuple[str, str], StageResolution] = {}
        for stage in stages:
            pipeline = pipeline_by_id.get(stage.pipeline_id)
            if pipeline is None:
                continue
            self._lookup[(pipeline.name, stage.name)] = StageResolution(
                pipeline_id=pipeline.id, stage_id=stage.id
            )

    def resolve(self, pipeline_name: str, stage_name: str) -> StageResolution | None:
        return self._lookup.get((pipeline_name, stage_name))
