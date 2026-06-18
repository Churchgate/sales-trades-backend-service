from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.endpoints.analytics import active_pipeline, overview, revenue
from app.repositories import analytics_repo, deals_repo, reference_repo
from app.schemas.analytics import AnalyticsFilters

LEASING = "WTC Abuja – Leasing"
CLUB = "Churchgate – Membership"


async def _seed(session: AsyncSession) -> None:
    await reference_repo.upsert_pipeline(
        session,
        {"id": 1, "name": "New Rental", "business_line": LEASING,
         "is_default": True, "is_active": True},
    )
    await reference_repo.upsert_pipeline(
        session,
        {"id": 2, "name": "Club C1", "business_line": CLUB, "is_default": False, "is_active": True},
    )
    stages = [
        (10, 1, "Negotiation", 1, "Open", 20),
        (11, 1, "Won", 9, "Closed Won", 100),
        (12, 1, "Lost", 9, "Closed Lost", 0),
        (20, 2, "Intro", 1, "Open", 50),
    ]
    for sid, pid, name, pos, ftype, prob in stages:
        await reference_repo.upsert_stage(
            session,
            {"id": sid, "pipeline_id": pid, "name": name, "position": pos,
             "forecast_type": ftype, "probability": prob},
        )
    for oid in (100, 101):
        await reference_repo.upsert_owner(
            session, {"id": oid, "display_name": f"Rep{oid}", "email": None, "is_active": True}
        )
    deals = [
        # id,   pipe, stage, owner, amount, close,            age, status
        (1, 1, 10, 100, 1000, date(2026, 7, 15), 10, None),
        (2, 1, 11, 100, 2000, date(2026, 6, 1), 30, None),      # won
        (3, 1, 12, 101, 500, None, 40, None),                   # lost
        (4, 2, 20, 101, 4000, date(2026, 7, 20), 800, None),    # dormant open
        (5, 1, 10, 100, 100, None, 5, "Dead deal"),             # dead open
    ]
    for did, pid, sid, oid, amount, close, age, cf_status in deals:
        await deals_repo.upsert_deal(
            session,
            {"deal_id": did, "pipeline_id": pid, "stage_id": sid, "owner_id": oid,
             "amount": amount, "expected_close_date": close, "age_days": age,
             "cf_deal_status": cf_status},
        )
    await session.commit()


async def test_overview_per_line_and_blended(db_session: AsyncSession) -> None:
    await _seed(db_session)
    resp = await overview(db_session, None, AnalyticsFilters())
    by_line = {h.business_line: h for h in resp.business_lines}

    leasing = by_line[LEASING]
    assert leasing.total_deals == 4  # d1, d2, d3, d5
    assert leasing.open_deals == 2 and leasing.open_value == 1100  # d1 + d5
    assert leasing.won_deals == 1 and leasing.won_value == 2000
    assert leasing.lost_deals == 1
    assert leasing.win_rate == 0.5  # 1 won / (1 won + 1 lost)

    club = by_line[CLUB]
    assert club.open_deals == 1 and club.win_rate is None  # nothing closed

    blended = by_line["All business lines"]
    assert blended.is_blended is True
    assert blended.total_deals == 5
    assert blended.open_value == 5100
    assert resp.data_as_of is not None


async def test_active_pipeline_excludes_dormant_and_dead(db_session: AsyncSession) -> None:
    await _seed(db_session)
    resp = await active_pipeline(db_session, None, AnalyticsFilters())
    assert resp.live_deals == 1  # only d1
    assert resp.live_value == 1000
    assert resp.excluded_count == 2
    reasons = {e.deal_id: e.reason for e in resp.excluded}
    assert reasons[4] == "dormant (age > 730d)"
    assert reasons[5] == "dead/duplicate"


async def test_revenue_buckets_by_month(db_session: AsyncSession) -> None:
    await _seed(db_session)
    resp = await revenue(db_session, None, AnalyticsFilters())
    rows = {(r.business_line, r.close_month): r for r in resp.months}

    jul = rows[(LEASING, "2026-07")]
    assert jul.open_value == 1000  # d1
    assert jul.weighted_open_value == 200  # 1000 * 20%
    jun = rows[(LEASING, "2026-06")]
    assert jun.won_value == 2000  # d2


async def test_business_line_filter_and_owner_scope(db_session: AsyncSession) -> None:
    await _seed(db_session)
    # Filter to one business line.
    only_club = await analytics_repo.get_overview(
        db_session, AnalyticsFilters(business_line=CLUB), None
    )
    assert {r["business_line"] for r in only_club} == {CLUB}

    # rep owner_scope=101 sees only its own deals (d3 lost, d4 open) regardless of filters.
    scoped = await analytics_repo.get_overview(db_session, AnalyticsFilters(), 101)
    totals = {r["business_line"]: r["total_deals"] for r in scoped}
    assert totals == {LEASING: 1, CLUB: 1}  # d3 in Leasing, d4 in Club
