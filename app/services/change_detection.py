from datetime import datetime
from typing import Any

from app.models.deal import DealSnapshot
from app.models.deal_event import DealEvent


def detect_changes(
    *,
    deal_id: int,
    existing: DealSnapshot | None,
    new_pipeline_id: int | None,
    new_stage_id: int | None,
    new_owner_id: int | None,
    occurred_at: datetime,
    source: str,
    raw_payload: dict[str, Any],
) -> list[DealEvent]:
    """Diff incoming state against the stored snapshot and build `deal_events` rows.

    The webhook payload is the deal's *current full state*, not a diff (spec §7),
    so changes are detected by comparing against the previously stored snapshot.
    """
    if existing is None:
        return [
            DealEvent(
                deal_id=deal_id,
                event_type="created",
                new_pipeline_id=new_pipeline_id,
                new_stage_id=new_stage_id,
                new_owner_id=new_owner_id,
                occurred_at=occurred_at,
                source=source,
                raw_payload=raw_payload,
            )
        ]

    events: list[DealEvent] = []

    if (existing.pipeline_id, existing.stage_id) != (new_pipeline_id, new_stage_id):
        events.append(
            DealEvent(
                deal_id=deal_id,
                event_type="stage_change",
                old_pipeline_id=existing.pipeline_id,
                old_stage_id=existing.stage_id,
                new_pipeline_id=new_pipeline_id,
                new_stage_id=new_stage_id,
                occurred_at=occurred_at,
                source=source,
                raw_payload=raw_payload,
            )
        )

    if existing.owner_id != new_owner_id:
        events.append(
            DealEvent(
                deal_id=deal_id,
                event_type="owner_change",
                old_owner_id=existing.owner_id,
                new_owner_id=new_owner_id,
                occurred_at=occurred_at,
                source=source,
                raw_payload=raw_payload,
            )
        )

    return events
