from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.endpoints.analytics import (
    ageing,
    lead_source,
    loss_reasons,
    next_actions,
    owners,
    staleness,
)
from app.models.deal_event import DealEvent
from app.models.email_activity import EmailActivity
from app.models.task import TaskSnapshot
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
    for sid, name, pos, ftype, prob in [
        (10, "Negotiation", 1, "Open", 20),
        (11, "Won", 9, "Closed Won", 100),
        (12, "Lost", 9, "Closed Lost", 0),
    ]:
        await reference_repo.upsert_stage(
            session,
            {"id": sid, "pipeline_id": 1, "name": name, "position": pos,
             "forecast_type": ftype, "probability": prob},
        )
    for oid in (100, 101):
        await reference_repo.upsert_owner(
            session, {"id": oid, "display_name": f"Rep{oid}", "email": None, "is_active": True}
        )
    # id, stage, owner, value, stage_updated(days ago), age, lost_reason
    deals = [
        (1, 10, 100, 1000, 60, 60, None),    # open, stale move, no activity
        (2, 10, 100, 2000, 5, 5, None),      # open, fresh, has task + email
        (3, 11, 100, 5000, 100, 100, None),  # won
        (4, 12, 101, 800, 200, 200, "Too expensive"),  # lost, pricing
        (5, 12, 101, 300, 400, 400, None),   # lost, no reason
        (6, 10, 101, 400, 90, 900, None),    # open, stale move, recent event
    ]
    for did, sid, oid, value, moved, age, lost in deals:
        await deals_repo.upsert_deal(
            session,
            {"deal_id": did, "pipeline_id": 1, "stage_id": sid, "owner_id": oid,
             "amount": value, "stage_updated_at": _ago(moved), "age_days": age,
             "lost_reason": lost},
        )
    # d2: an open task (past due) + a recent email -> active, has next action.
    session.add(TaskSnapshot(task_id=1, deal_id=2, owner_id=100, status="open", due_date=_ago(1)))
    session.add(EmailActivity(deal_id=2, direction="outgoing", conversation_time=_ago(2)))
    # d6: a stage-change event 10 days ago -> progressed recently, recent activity.
    session.add(
        DealEvent(deal_id=6, event_type="stage_change", occurred_at=_ago(10), source="webhook")
    )
    await session.commit()


async def test_staleness_by_stage_and_owner(db_session: AsyncSession) -> None:
    await _seed(db_session)
    resp = await staleness(db_session, None, AnalyticsFilters())
    assert resp.open_deals == 3  # d1, d2, d6
    assert resp.stale_by_stage_move == 2  # d1 (60d), d6 (90d); d2 (5d) fresh
    assert resp.stale_value == 1400  # 1000 + 400
    assert resp.no_activity == 1  # only d1 (d2 email 2d, d6 event 10d)
    assert str(resp.stale_days) in resp.definition
    owners_map = {b.key: b for b in resp.by_owner}
    assert owners_map["Rep100"].stale_deals == 1 and owners_map["Rep100"].stale_value == 1000
    assert owners_map["Rep101"].stale_deals == 1


async def test_owner_accountability(db_session: AsyncSession) -> None:
    await _seed(db_session)
    resp = await owners(db_session, None, AnalyticsFilters())
    by_id = {o.owner_id: o for o in resp.owners}

    rep100 = by_id[100]
    assert rep100.open_deals == 2 and rep100.won_deals == 1
    assert rep100.win_rate == 1.0  # 1 won / 0 lost
    assert rep100.stale_value == 1000  # d1
    assert rep100.no_next_action == 1  # d1 has no open task
    assert rep100.no_recent_activity == 1  # d1
    assert rep100.deals_progressed == 0

    rep101 = by_id[101]
    assert rep101.win_rate == 0.0  # 0 won / 2 lost
    assert rep101.deals_progressed == 1  # d6 stage-change in last 30d
    assert rep101.no_recent_activity == 0  # d6 event 10d ago


async def test_next_actions(db_session: AsyncSession) -> None:
    await _seed(db_session)
    resp = await next_actions(db_session, None, AnalyticsFilters())
    assert resp.open_deals == 3
    assert resp.with_next_task == 1  # only d2
    assert resp.with_recent_activity == 2  # d2 + d6
    assert resp.with_next_task_pct == 33.3


async def test_loss_reasons_categorised(db_session: AsyncSession) -> None:
    await _seed(db_session)
    resp = await loss_reasons(db_session, None, AnalyticsFilters())
    assert resp.total_lost == 2
    assert resp.no_reason_pct == 50.0
    cats = {c.category: c for c in resp.categories}
    assert cats["pricing"].lost_deals == 1 and cats["pricing"].lost_value == 800
    assert "no reason recorded" in cats


async def test_ageing_buckets(db_session: AsyncSession) -> None:
    await _seed(db_session)
    resp = await ageing(db_session, None, AnalyticsFilters())
    stage = {r.key: r for r in resp.by_stage}["Negotiation"]
    assert stage.bucket_0_30 == 1  # d2 (5)
    assert stage.bucket_30_90 == 1  # d1 (60)
    assert stage.bucket_365_plus == 1  # d6 (900)
    owner = {r.key: r for r in resp.by_owner}
    assert owner["Rep101"].bucket_365_plus == 1


async def test_lead_source_unavailable(db_session: AsyncSession) -> None:
    await _seed(db_session)
    resp = await lead_source(db_session, None, AnalyticsFilters())
    assert resp.available is False
    assert resp.reason is not None
