from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.endpoints.analytics import response_times
from app.models.email_activity import EmailActivity
from app.repositories import deals_repo, reference_repo
from app.schemas.analytics import AnalyticsFilters

NOW = datetime.now(UTC)


def _ago(days: int) -> datetime:
    return NOW - timedelta(days=days)


async def _seed(session: AsyncSession) -> None:
    await reference_repo.upsert_pipeline(
        session,
        {"id": 1, "name": "Rental", "business_line": "Leasing", "is_default": True,
         "is_active": True},
    )
    for oid in (100, 101):
        await reference_repo.upsert_owner(
            session, {"id": oid, "display_name": f"Rep{oid}", "email": None, "is_active": True}
        )
    # owner 100: two deals, both with outreach (2-day and 5-day response).
    await deals_repo.upsert_deal(
        session, {"deal_id": 1, "pipeline_id": 1, "owner_id": 100, "deal_created_at": _ago(10)}
    )
    await deals_repo.upsert_deal(
        session, {"deal_id": 2, "pipeline_id": 1, "owner_id": 100, "deal_created_at": _ago(20)}
    )
    # owner 101: one deal, no outgoing email at all.
    await deals_repo.upsert_deal(
        session, {"deal_id": 3, "pipeline_id": 1, "owner_id": 101, "deal_created_at": _ago(5)}
    )
    session.add(EmailActivity(deal_id=1, direction="outgoing", conversation_time=_ago(8)))  # 2d
    session.add(EmailActivity(deal_id=2, direction="outgoing", conversation_time=_ago(15)))  # 5d
    # An incoming email must not count as outreach.
    session.add(EmailActivity(deal_id=3, direction="incoming", conversation_time=_ago(4)))
    await session.commit()


async def test_response_times_per_owner_and_overall(db_session: AsyncSession) -> None:
    await _seed(db_session)
    resp = await response_times(db_session, None, AnalyticsFilters())
    by_id = {o.owner_id: o for o in resp.owners}

    rep100 = by_id[100]
    assert rep100.deals_with_outreach == 2
    assert rep100.deals_without_outreach == 0
    assert rep100.avg_first_response_days == 3.5  # (2 + 5) / 2

    rep101 = by_id[101]
    assert rep101.deals_with_outreach == 0
    assert rep101.deals_without_outreach == 1  # incoming-only doesn't count
    assert rep101.avg_first_response_days is None

    assert resp.overall_avg_first_response_days == 3.5  # only rep100's 2 outreach deals
