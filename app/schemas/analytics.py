from datetime import date

from pydantic import BaseModel, Field


class AnalyticsFilters(BaseModel):
    """Shared query parameters for the analytics endpoints.

    `business_line` / `pipeline_id` / `owner_id` narrow an aggregation; `stale_days`
    sets the staleness threshold; `as_of` pins the reporting date. Owner scoping for
    `rep` users is enforced separately via `OwnerScopeDep` (see `app/api/dependencies.py`),
    not through this model — a rep cannot widen their view by passing `owner_id`.
    """

    business_line: str | None = None
    pipeline_id: int | None = None
    owner_id: int | None = None
    stale_days: int = Field(default=30, ge=1, le=365)
    as_of: date | None = None
