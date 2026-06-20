from datetime import date, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.endpoints.analytics import renewals
from app.repositories import deals_repo, reference_repo
from app.schemas.analytics import AnalyticsFilters

TODAY = date.today()


async def _seed(session: AsyncSession) -> None:
    await reference_repo.upsert_pipeline(
        session,
        {"id": 1, "name": "Renewal", "business_line": "Leasing", "is_default": True,
         "is_active": True},
    )
    for sid, ftype in [(10, "Open"), (12, "Closed Lost")]:
        await reference_repo.upsert_stage(
            session,
            {"id": sid, "pipeline_id": 1, "name": ftype, "position": 1,
             "forecast_type": ftype, "probability": 0},
        )
    # id, stage, term_end_date, lease_amount
    deals = [
        (1, 10, TODAY + timedelta(days=30), 5000),    # upcoming, in window
        (2, 10, TODAY + timedelta(days=200), 9000),   # beyond default 90-day window
        (3, 10, TODAY - timedelta(days=10), 4000),    # overdue (within 30-day grace)
        (4, 12, TODAY + timedelta(days=30), 1000),    # Closed Lost -> excluded
        (5, 10, None, 2000),                          # no term end date -> excluded
    ]
    for did, sid, term_end, amount in deals:
        await deals_repo.upsert_deal(
            session,
            {"deal_id": did, "pipeline_id": 1, "stage_id": sid,
             "cf_term_end_date": term_end, "cf_total_lease_amount": amount},
        )
    await session.commit()


async def test_renewals_window_and_overdue(db_session: AsyncSession) -> None:
    await _seed(db_session)
    resp = await renewals(db_session, None, AnalyticsFilters(), within_days=90)

    ids = {d.deal_id for d in resp.renewals}
    assert ids == {1, 3}  # d2 beyond window, d4 lost, d5 no date
    assert resp.upcoming_count == 1  # d1
    assert resp.overdue_count == 1  # d3 (term ended 10 days ago)
    assert resp.total_value == 9000  # 5000 + 4000

    by_id = {d.deal_id: d for d in resp.renewals}
    assert by_id[1].days_until_expiry == 30
    assert by_id[3].days_until_expiry == -10
    # most-urgent first: overdue d3 before upcoming d1
    assert [d.deal_id for d in resp.renewals] == [3, 1]


async def test_renewals_window_widens_with_within_days(db_session: AsyncSession) -> None:
    await _seed(db_session)
    resp = await renewals(db_session, None, AnalyticsFilters(), within_days=365)
    assert {d.deal_id for d in resp.renewals} == {1, 2, 3}  # d2 now inside the window
