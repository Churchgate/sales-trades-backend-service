from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.deal_event import DealEvent
from app.models.email_activity import EmailActivity
from app.models.task import TaskSnapshot
from app.repositories import analytics_repo, deals_repo, reference_repo


async def test_last_activity_at_is_max_across_all_sources(db_session: AsyncSession) -> None:
    await reference_repo.upsert_pipeline(
        db_session,
        {"id": 1, "name": "P", "business_line": "X", "is_default": True, "is_active": True},
    )
    await deals_repo.upsert_deal(
        db_session,
        {"deal_id": 1001, "pipeline_id": 1,
         "stage_updated_at": datetime(2026, 1, 1, tzinfo=UTC)},
    )
    db_session.add(
        TaskSnapshot(
            task_id=1, deal_id=1001, status="open", due_date=datetime(2026, 2, 1, tzinfo=UTC)
        )
    )
    db_session.add(
        EmailActivity(deal_id=1001, direction="incoming",
                      conversation_time=datetime(2026, 3, 1, tzinfo=UTC))
    )
    # The newest signal — the answer should be this.
    db_session.add(
        DealEvent(deal_id=1001, event_type="stage_change",
                  occurred_at=datetime(2026, 4, 1, tzinfo=UTC), source="webhook")
    )
    await db_session.commit()

    assert await analytics_repo.get_last_activity_at(db_session, 1001) == datetime(
        2026, 4, 1, tzinfo=UTC
    )


async def test_last_activity_at_none_for_unknown_deal(db_session: AsyncSession) -> None:
    assert await analytics_repo.get_last_activity_at(db_session, 999) is None
