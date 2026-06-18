import httpx
import respx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.freshsales.client import FreshsalesClient
from app.models.task import TaskSnapshot
from app.repositories import deals_repo, reference_repo
from app.services.task_sync import run_task_sync

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
    await reference_repo.upsert_owner(
        session, {"id": 100, "display_name": "Rep", "email": "r@x.com", "is_active": True}
    )
    await deals_repo.upsert_deal(
        session, {"deal_id": deal_id, "pipeline_id": 1, "stage_id": 10, "owner_id": 100}
    )
    await session.commit()


async def test_task_sync_upserts_open_deal_tasks(db_session: AsyncSession) -> None:
    await _seed_open_deal(db_session)
    payload = {
        "tasks": [
            {"id": 5001, "title": "Follow up", "owner_id": 100, "status": 0,
             "due_date": "2024-03-28T09:15:00+00:00", "completed_date": None},
            {"id": 5002, "title": "Done", "owner_id": 100, "status": 1,
             "due_date": "2024-03-01T09:00:00+00:00",
             "completed_date": "2024-03-02T10:00:00+00:00"},
        ]
    }
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        router.get(url__regex=r".*/crm/sales/deals/\d+/tasks").mock(
            return_value=httpx.Response(200, json=payload)
        )
        async with FreshsalesClient() as client:
            await run_task_sync(db_session, client)

    rows = {r.task_id: r for r in (await db_session.execute(select(TaskSnapshot))).scalars().all()}
    assert set(rows) == {5001, 5002}
    assert rows[5001].status == "open"
    assert rows[5001].deal_id == 1001
    assert rows[5001].completed_date is None
    assert rows[5002].status == "completed"
    assert rows[5002].completed_date is not None


async def test_task_sync_scopes_to_open_deals_only(db_session: AsyncSession) -> None:
    await _seed_open_deal(db_session, deal_id=1001)
    # A second deal in a Closed-Won stage must be skipped (not fetched).
    await reference_repo.upsert_stage(
        db_session,
        {"id": 20, "pipeline_id": 1, "name": "Won", "position": 9,
         "forecast_type": "Closed Won", "probability": 100},
    )
    await deals_repo.upsert_deal(db_session, {"deal_id": 2002, "pipeline_id": 1, "stage_id": 20})
    await db_session.commit()

    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        route = router.get(url__regex=r".*/crm/sales/deals/\d+/tasks").mock(
            return_value=httpx.Response(200, json={"tasks": []})
        )
        async with FreshsalesClient() as client:
            await run_task_sync(db_session, client)

    # Only the open deal (1001) should have been queried.
    assert route.call_count == 1
    assert route.calls[0].request.url.path == "/crm/sales/deals/1001/tasks"
