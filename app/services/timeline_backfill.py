"""Timeline backfill (spec §6C / §9 #5): seed `deal_events` history from the
Freshsales timeline feed so trend charts have real history on day one.

For each deal we pull `/deals/{id}/timeline_feeds` (paginated via `meta.has_next`)
and extract STAGE_CHANGE / OWNER_CHANGE entries — these carry their ids directly in
`action_data` (verified live: STAGE_CHANGE → `{stage_id, stage_name, pipeline_name}`,
OWNER_CHANGE → `{owner_change, owner_name}`), so resolution is simpler than webhooks.

We filter by `action_type`, which is reliable for stage/owner changes. The §7
corruption (~91% of malformed `action_type`) is specific to *task* events from the
"Deal Followup Remainder" workflow, which we don't extract here.
"""

from collections.abc import Iterable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.freshsales.client import FreshsalesClient
from app.freshsales.parsing import parse_iso_timestamp
from app.models.deal_event import DealEvent
from app.repositories import events_repo, reference_repo

logger = get_logger(__name__)

TIMELINE_SOURCE = "timeline_backfill"


def _parse_stage_change(
    feed: dict[str, Any], deal_id: int, pipeline_id_by_name: dict[str, int]
) -> DealEvent | None:
    occurred_at = parse_iso_timestamp(feed.get("created_at"))
    if occurred_at is None:
        return None
    action_data = feed.get("action_data") or {}
    return DealEvent(
        deal_id=deal_id,
        event_type="stage_change",
        new_stage_id=action_data.get("stage_id"),
        new_pipeline_id=pipeline_id_by_name.get(action_data.get("pipeline_name")),
        occurred_at=occurred_at,
        source=TIMELINE_SOURCE,
        raw_payload=feed,
    )


def _parse_owner_change(feed: dict[str, Any], deal_id: int) -> DealEvent | None:
    occurred_at = parse_iso_timestamp(feed.get("created_at"))
    if occurred_at is None:
        return None
    action_data = feed.get("action_data") or {}
    return DealEvent(
        deal_id=deal_id,
        event_type="owner_change",
        new_owner_id=action_data.get("owner_change"),
        occurred_at=occurred_at,
        source=TIMELINE_SOURCE,
        raw_payload=feed,
    )


async def run_timeline_backfill(
    session: AsyncSession, client: FreshsalesClient, deal_ids: Iterable[int]
) -> None:
    """Backfill `deal_events` (source=`timeline_backfill`) for the given deals.

    Idempotent per deal: existing backfill events for a deal are cleared before its
    fresh set is inserted, so re-running never duplicates and never touches
    webhook-sourced events.
    """
    pipelines = await reference_repo.list_pipelines(session)
    pipeline_id_by_name = {p.name: p.id for p in pipelines}
    counters = {"deals": 0, "events": 0}

    for deal_id in deal_ids:
        events: list[DealEvent] = []
        async for feed in client.paginate_timeline(deal_id):
            action_type = feed.get("action_type")
            if action_type == "STAGE_CHANGE":
                event = _parse_stage_change(feed, deal_id, pipeline_id_by_name)
            elif action_type == "OWNER_CHANGE":
                event = _parse_owner_change(feed, deal_id)
            else:
                event = None
            if event is not None:
                events.append(event)

        await events_repo.delete_events_by_source(session, deal_id, TIMELINE_SOURCE)
        for event in events:
            session.add(event)
        counters["deals"] += 1
        counters["events"] += len(events)
        await session.commit()

    logger.info("timeline backfill complete", deals=counters["deals"], events=counters["events"])
