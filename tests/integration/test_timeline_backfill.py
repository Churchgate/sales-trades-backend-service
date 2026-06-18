from datetime import UTC, datetime

import httpx
import respx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.freshsales.client import FreshsalesClient
from app.models.deal_event import DealEvent
from app.repositories import deals_repo, events_repo, reference_repo
from app.services.timeline_backfill import run_timeline_backfill

BASE_URL = "https://rbpropertieslimited.myfreshworks.com"

# Mirrors the live timeline_feeds shape (verified): list under `timeline_feeds`,
# STAGE_CHANGE/OWNER_CHANGE carry their ids in action_data.
_FEED_PAYLOAD = {
    "timeline_feeds": [
        {"action_type": "STAGE_CHANGE", "created_at": "2026-05-18T17:38:03+01:00",
         "targetable_id": 1001,
         "action_data": {"stage_id": 10, "stage_name": "Invoice Sent",
                         "pipeline_name": "Renewal Pipeline"}},
        {"action_type": "OWNER_CHANGE", "created_at": "2024-03-20T16:51:58+00:00",
         "action_data": {"owner_change": 100, "owner_name": "Banke"}},
        # Non stage/owner events are ignored.
        {"action_type": "CREATE", "created_at": "2024-01-01T00:00:00+00:00", "action_data": {}},
        {"action_type": "TASK_CREATED", "actionable_type": "Task",
         "created_at": "2024-02-01T00:00:00+00:00", "action_data": {}},
    ],
    "meta": {"has_next": False},
}


async def _seed_deal(session: AsyncSession, *, deal_id: int = 1001) -> None:
    await reference_repo.upsert_pipeline(
        session,
        {"id": 1, "name": "Renewal Pipeline", "business_line": "X",
         "is_default": False, "is_active": True},
    )
    await reference_repo.upsert_stage(
        session,
        {"id": 10, "pipeline_id": 1, "name": "Invoice Sent", "position": 5,
         "forecast_type": "Open", "probability": 50},
    )
    await deals_repo.upsert_deal(session, {"deal_id": deal_id, "pipeline_id": 1, "stage_id": 10})
    await session.commit()


async def _backfill_events(session: AsyncSession, deal_id: int) -> list[DealEvent]:
    stmt = select(DealEvent).where(
        DealEvent.deal_id == deal_id, DealEvent.source == "timeline_backfill"
    )
    return list((await session.execute(stmt)).scalars().all())


async def test_backfill_extracts_stage_and_owner_changes(db_session: AsyncSession) -> None:
    await _seed_deal(db_session)
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        router.get(url__regex=r".*/timeline_feeds").mock(
            return_value=httpx.Response(200, json=_FEED_PAYLOAD)
        )
        async with FreshsalesClient() as client:
            await run_timeline_backfill(db_session, client, [1001])

    events = {e.event_type: e for e in await _backfill_events(db_session, 1001)}
    assert set(events) == {"stage_change", "owner_change"}  # CREATE + task ignored
    assert events["stage_change"].new_stage_id == 10
    assert events["stage_change"].new_pipeline_id == 1  # resolved from pipeline_name
    assert events["stage_change"].occurred_at == datetime(2026, 5, 18, 16, 38, 3, tzinfo=UTC)
    assert events["owner_change"].new_owner_id == 100


async def test_backfill_is_idempotent_and_preserves_webhook_events(
    db_session: AsyncSession,
) -> None:
    await _seed_deal(db_session)
    # A pre-existing webhook event must survive the backfill.
    await events_repo.insert_event(
        db_session,
        DealEvent(deal_id=1001, event_type="stage_change",
                  occurred_at=datetime(2026, 1, 1, tzinfo=UTC), source="webhook"),
    )
    await db_session.commit()

    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        router.get(url__regex=r".*/timeline_feeds").mock(
            return_value=httpx.Response(200, json=_FEED_PAYLOAD)
        )
        async with FreshsalesClient() as client:
            await run_timeline_backfill(db_session, client, [1001])
            await run_timeline_backfill(db_session, client, [1001])  # re-run

    assert len(await _backfill_events(db_session, 1001)) == 2  # not 4
    webhook = (
        await db_session.execute(
            select(DealEvent).where(DealEvent.deal_id == 1001, DealEvent.source == "webhook")
        )
    ).scalars().all()
    assert len(webhook) == 1  # untouched


async def test_list_deal_ids_without_events(db_session: AsyncSession) -> None:
    await _seed_deal(db_session, deal_id=1001)
    await deals_repo.upsert_deal(db_session, {"deal_id": 2002, "pipeline_id": 1, "stage_id": 10})
    await events_repo.insert_event(
        db_session,
        DealEvent(deal_id=1001, event_type="created",
                  occurred_at=datetime(2026, 1, 1, tzinfo=UTC), source="webhook"),
    )
    await db_session.commit()

    ids = await events_repo.list_deal_ids_without_events(db_session)
    assert ids == [2002]
