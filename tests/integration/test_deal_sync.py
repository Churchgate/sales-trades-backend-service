import httpx
import pytest
import respx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.freshsales.client import FreshsalesClient
from app.repositories import deals_repo, reference_repo
from app.services.deal_sync import run_deal_sync

BASE_URL = "https://rbpropertieslimited.myfreshworks.com"


async def _seed_reference(session: AsyncSession) -> None:
    await reference_repo.upsert_pipeline(
        session,
        {"id": 1, "name": "Default", "business_line": "X", "is_default": True, "is_active": True},
    )
    await reference_repo.upsert_pipeline(
        session,
        {"id": 2, "name": "Other", "business_line": "Y", "is_default": False, "is_active": True},
    )
    for sid, pid in [(10, 1), (20, 2)]:
        await reference_repo.upsert_stage(
            session,
            {"id": sid, "pipeline_id": pid, "name": "New", "position": 1,
             "forecast_type": "Open", "probability": 10},
        )
    await reference_repo.upsert_owner(
        session, {"id": 100, "display_name": "Rep", "email": "r@x.com", "is_active": True}
    )
    await session.commit()


async def test_deal_sync_covers_default_and_non_default_pipelines(
    db_session: AsyncSession,
) -> None:
    await _seed_reference(db_session)
    view_ids = get_settings().deal_view_ids

    def view_side_effect(request: httpx.Request) -> httpx.Response:
        # Default pipeline served via the first configured view, page 1 only.
        first_view = f"/crm/sales/api/deals/view/{view_ids[0]}"
        if request.url.path == first_view and request.url.params.get("page") == "1":
            return httpx.Response(
                200,
                json={"deals": [
                    {"id": 1001, "deal_pipeline_id": 1, "deal_stage_id": 10,
                     "owner_id": 100, "amount": "500", "name": "Default deal"}
                ]},
            )
        return httpx.Response(200, json={"deals": []})

    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        router.get(url__regex=r".*/deals/view/\d+").mock(side_effect=view_side_effect)
        # Non-default pipeline 2: filtered_search yields one id, detail returns the record.
        router.post(url__regex=r".*/filtered_search/deal").mock(
            return_value=httpx.Response(200, json={"deals": [{"id": 2002}], "meta": {"total": 1}})
        )
        router.get(url__regex=r".*/deals/2002").mock(
            return_value=httpx.Response(
                200,
                json={"deal": {"id": 2002, "deal_pipeline_id": 2, "deal_stage_id": 20,
                               "owner_id": 100, "amount": "900", "name": "Other deal"}},
            )
        )

        async with FreshsalesClient() as client:
            await run_deal_sync(db_session, client)

    default_deals = await deals_repo.list_deals_for_pipeline(db_session, 1)
    other_deals = await deals_repo.list_deals_for_pipeline(db_session, 2)
    assert {d.deal_id for d in default_deals} == {1001}
    assert {d.deal_id for d in other_deals} == {2002}
    assert other_deals[0].stage_id == 20
    assert other_deals[0].owner_id == 100


async def test_deal_sync_commits_in_batches_surviving_a_later_failure(
    db_session: AsyncSession,
) -> None:
    """A non-default pipeline syncs one request per deal, so a full pipeline can run
    long enough to hit a transient failure partway through. Before batched commits,
    the whole pipeline's progress lived in one uncommitted transaction and a failure
    on deal N lost deals 1..N-1 too. Seed 30 deals (> COMMIT_BATCH_SIZE=25) and fail
    on deal 26 — the first 25 must already be persisted despite the raised error."""
    await _seed_reference(db_session)
    deal_ids = list(range(2001, 2031))  # 30 deals in the non-default pipeline

    deals_meta = {"deals": [{"id": did} for did in deal_ids], "meta": {"total": len(deal_ids)}}

    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        router.get(url__regex=r".*/deals/view/\d+").mock(
            return_value=httpx.Response(200, json={"deals": []})
        )
        router.post(url__regex=r".*/filtered_search/deal").mock(
            return_value=httpx.Response(200, json=deals_meta)
        )

        def detail_side_effect(request: httpx.Request) -> httpx.Response:
            deal_id = int(request.url.path.rsplit("/", 1)[-1])
            if deal_id == deal_ids[25]:  # the 26th deal — one past the first batch
                raise httpx.ConnectError("simulated transient failure")
            return httpx.Response(
                200,
                json={"deal": {"id": deal_id, "deal_pipeline_id": 2, "deal_stage_id": 20,
                               "owner_id": 100, "amount": "100", "name": f"Deal {deal_id}"}},
            )

        router.get(url__regex=r".*/deals/\d+").mock(side_effect=detail_side_effect)

        async with FreshsalesClient() as client:
            with pytest.raises(httpx.ConnectError):
                await run_deal_sync(db_session, client)

    synced = await deals_repo.list_deals_for_pipeline(db_session, 2)
    assert len(synced) == 25  # first batch committed; the failed deal + rest never landed
    assert {d.deal_id for d in synced} == set(deal_ids[:25])
