from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.endpoints.analytics import data_quality, leads_status
from app.repositories import deals_repo, reference_repo
from app.schemas.analytics import AnalyticsFilters


async def _seed(session: AsyncSession) -> None:
    await reference_repo.upsert_pipeline(
        session,
        {"id": 1, "name": "P", "business_line": "BL", "is_default": True, "is_active": True},
    )
    await reference_repo.upsert_stage(
        session,
        {"id": 10, "pipeline_id": 1, "name": "S", "position": 1,
         "forecast_type": "Open", "probability": 10},
    )
    await reference_repo.upsert_owner(
        session, {"id": 100, "display_name": "Rep", "email": None, "is_active": True}
    )
    # d1, d2 share a name (duplicate); d3 missing owner+stage; d4 missing value.
    await deals_repo.upsert_deal(
        session, {"deal_id": 1, "pipeline_id": 1, "stage_id": 10, "owner_id": 100,
                  "name": "Acme Tower", "amount": 100},
    )
    await deals_repo.upsert_deal(
        session, {"deal_id": 2, "pipeline_id": 1, "stage_id": 10, "owner_id": 100,
                  "name": "acme tower ", "amount": 200},  # case/space variant -> duplicate
    )
    await deals_repo.upsert_deal(
        session, {"deal_id": 3, "pipeline_id": 1, "name": "Lonely", "amount": 50},  # no owner/stage
    )
    await deals_repo.upsert_deal(
        session, {"deal_id": 4, "pipeline_id": 1, "stage_id": 10, "owner_id": 100,
                  "name": "NoValue"},  # missing value
    )
    await session.commit()


async def test_data_quality_counts(db_session: AsyncSession) -> None:
    await _seed(db_session)
    resp = await data_quality(db_session, None, AnalyticsFilters())
    assert resp.total_deals == 4
    assert resp.missing_owner == 1  # d3
    assert resp.missing_stage == 1  # d3
    assert resp.missing_value == 1  # d4
    assert resp.duplicate_name_deals == 2  # d1 + d2 (same name, case/space-insensitive)
    assert resp.notes


async def test_leads_status_flags_gap(db_session: AsyncSession) -> None:
    resp = await leads_status(db_session)
    assert resp.leads_ingested is False
    assert "leads module" in resp.message.lower()
