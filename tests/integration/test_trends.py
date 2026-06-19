from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.endpoints.analytics import trends
from app.models.pipeline_daily_snapshot import PipelineDailySnapshot
from app.repositories import deals_repo, reference_repo
from app.schemas.analytics import AnalyticsFilters
from app.services.daily_snapshot import run_daily_snapshot

TODAY = date.today()


async def _seed_reference(session: AsyncSession) -> None:
    await reference_repo.upsert_pipeline(
        session,
        {"id": 1, "name": "Rental", "business_line": "Leasing", "is_default": True,
         "is_active": True},
    )
    await reference_repo.upsert_stage(
        session,
        {"id": 10, "pipeline_id": 1, "name": "Negotiation", "position": 1,
         "forecast_type": "Open", "probability": 20},
    )
    await session.commit()


async def test_daily_snapshot_rollup_is_idempotent(db_session: AsyncSession) -> None:
    await _seed_reference(db_session)
    await deals_repo.upsert_deal(
        db_session, {"deal_id": 1, "pipeline_id": 1, "stage_id": 10, "amount": 1000}
    )
    await deals_repo.upsert_deal(
        db_session, {"deal_id": 2, "pipeline_id": 1, "stage_id": 10, "amount": 2000}
    )
    await db_session.commit()

    await run_daily_snapshot(db_session)
    await run_daily_snapshot(db_session)  # re-run must update, not duplicate

    rows = (
        await db_session.execute(select(PipelineDailySnapshot))
    ).scalars().all()
    assert len(rows) == 1  # one (date, pipeline, stage) row
    assert rows[0].snapshot_date == TODAY
    assert rows[0].deal_count == 2
    assert rows[0].total_value == 3000


async def test_trends_week_over_week(db_session: AsyncSession) -> None:
    await _seed_reference(db_session)
    eight_ago = TODAY - timedelta(days=8)
    db_session.add(
        PipelineDailySnapshot(snapshot_date=eight_ago, pipeline_id=1, stage_id=10,
                              deal_count=3, total_value=3000, total_base_currency_value=3000)
    )
    db_session.add(
        PipelineDailySnapshot(snapshot_date=TODAY, pipeline_id=1, stage_id=10,
                              deal_count=5, total_value=5000, total_base_currency_value=5000)
    )
    await db_session.commit()

    resp = await trends(db_session, None, AnalyticsFilters())
    assert resp.current_date == TODAY
    assert resp.comparison_date == eight_ago
    assert [p.snapshot_date for p in resp.series] == [eight_ago, TODAY]
    assert [p.total_value for p in resp.series] == [3000, 5000]

    wow = {r.stage_name: r for r in resp.week_over_week}["Negotiation"]
    assert wow.current_value == 5000
    assert wow.prev_value == 3000
    assert wow.value_delta == 2000
