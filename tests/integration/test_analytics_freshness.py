from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.email_activity import EmailActivity
from app.models.task import TaskSnapshot
from app.repositories import analytics_repo, deals_repo, reference_repo


async def test_data_as_of_is_null_without_deals(db_session: AsyncSession) -> None:
    assert await analytics_repo.get_data_as_of(db_session) is None


async def test_data_freshness_reports_each_source(db_session: AsyncSession) -> None:
    await reference_repo.upsert_pipeline(
        db_session,
        {"id": 1, "name": "P", "business_line": "X", "is_default": True, "is_active": True},
    )
    await reference_repo.upsert_owner(
        db_session, {"id": 100, "display_name": "Rep", "email": "r@x.com", "is_active": True}
    )
    await deals_repo.upsert_deal(
        db_session, {"deal_id": 1001, "pipeline_id": 1, "owner_id": 100, "amount": 500}
    )
    email_time = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
    db_session.add(TaskSnapshot(task_id=1, deal_id=1001, owner_id=100, status="open"))
    db_session.add(EmailActivity(deal_id=1001, direction="outgoing", conversation_time=email_time))
    await db_session.commit()

    freshness = await analytics_repo.get_data_freshness(db_session)

    assert freshness.data_as_of is not None
    # data_as_of is the canonical watermark and equals the deal sync time.
    assert freshness.deals_synced_at == freshness.data_as_of
    assert freshness.reference_synced_at is not None
    assert freshness.tasks_synced_at is not None
    assert freshness.email_last_activity_at == email_time
