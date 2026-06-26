import httpx
import respx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.freshsales.client import FreshsalesClient
from app.models.email_activity import EmailActivity
from app.repositories import deals_repo, reference_repo
from app.services.email_sync import run_email_sync

BASE_URL = "https://rbpropertieslimited.myfreshworks.com"


async def _seed_open_deal(session: AsyncSession, *, deal_id: int = 1001) -> None:
    await reference_repo.upsert_pipeline(
        session,
        {"id": 1, "name": "P", "business_line": "X", "is_default": True, "is_active": True},
    )
    await reference_repo.upsert_stage(
        session,
        {"id": 10, "pipeline_id": 1, "name": "New", "position": 1,
         "forecast_type": "Open", "probability": 10},
    )
    await deals_repo.upsert_deal(session, {"deal_id": deal_id, "pipeline_id": 1, "stage_id": 10})
    await session.commit()


async def test_email_sync_persists_strips_and_is_idempotent(db_session: AsyncSession) -> None:
    await _seed_open_deal(db_session)
    payload = {
        "email_conversations": [
            {"id": 9001, "direction": "incoming", "conversation_time": "2026-06-10T11:36:55+01:00",
             "subject": "Re: X", "html_content": "<big>body</big>",
             "current_html_content": "<big>body2</big>"},
            {"id": 9002, "direction": "outgoing", "conversation_time": "2026-06-09T08:00:00+00:00",
             "subject": "Y", "html_content": "<big>body</big>"},
            # No timestamp -> dropped (can't contribute to response-time/last-activity).
            {"id": 9003, "direction": "incoming", "conversation_time": None},
        ]
    }
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        router.get(url__regex=r".*/crm/sales/deals/\d+/conversations/all").mock(
            return_value=httpx.Response(200, json=payload)
        )
        async with FreshsalesClient() as client:
            await run_email_sync(db_session, client)
            await run_email_sync(db_session, client)  # second run must not duplicate

    rows = (await db_session.execute(select(EmailActivity))).scalars().all()
    assert len(rows) == 2  # idempotent; null-timestamp record dropped
    assert {r.direction for r in rows} == {"incoming", "outgoing"}
    for r in rows:
        assert "html_content" not in r.raw_payload
        assert "current_html_content" not in r.raw_payload
        assert r.raw_payload["id"] in (9001, 9002)
