from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.deal_event import DealEvent
from app.repositories import deals_repo, reference_repo
from app.services.reference_sync import build_stage_resolver
from app.services.webhook_ingest import ingest_webhook


async def _seed_reference_data(db_session: AsyncSession) -> None:
    await reference_repo.upsert_pipeline(
        db_session,
        {
            "id": 1,
            "name": "New Rental Pipeline",
            "business_line": "WTC Abuja - Leasing",
            "is_default": True,
            "is_active": True,
        },
    )
    await reference_repo.upsert_stage(
        db_session,
        {
            "id": 10,
            "pipeline_id": 1,
            "name": "New",
            "position": 1,
            "forecast_type": "Open",
            "probability": 10,
        },
    )
    await reference_repo.upsert_stage(
        db_session,
        {
            "id": 11,
            "pipeline_id": 1,
            "name": "Won",
            "position": 2,
            "forecast_type": "Closed Won",
            "probability": 100,
        },
    )
    await reference_repo.upsert_owner(
        db_session,
        {"id": 100, "display_name": "Rep A", "email": "rep.a@example.com", "is_active": True},
    )
    await db_session.commit()


def _base_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "deal_id": 5001,
        "deal_name": "WTC Tower A - Unit 501",
        "deal_amount": 50000.0,
        "deal_base_currency_amount": 50000.0,
        "deal_owner_id": 100,
        "deal_deal_pipeline_name": "New Rental Pipeline",
        "deal_deal_stage_name": "New",
        "deal_stage_updated_time": "06-15-2026 14:30:00",
        "deal_expected_close": "07-01-2026",
        "deal_sales_account_id": 9001,
        "deal_sales_account_name": "Acme Corp",
        "deal_sales_account_phone": "+2341234567",
        "deal_cf_project": "WTC Tower A",
        "deal_cf_sqm_size": "120.5",
        "deal_cf_term_end_date": "01-01-2027",
        "deal_cf_gross_rate": "5000",
    }
    payload.update(overrides)
    return payload


async def _events_for_deal(db_session: AsyncSession, deal_id: int) -> list[DealEvent]:
    result = await db_session.execute(
        select(DealEvent).where(DealEvent.deal_id == deal_id).order_by(DealEvent.id)
    )
    return list(result.scalars().all())


async def test_ingest_webhook_creates_deal_and_event(db_session: AsyncSession) -> None:
    await _seed_reference_data(db_session)
    resolver = await build_stage_resolver(db_session)

    await ingest_webhook(db_session, _base_payload(), resolver)

    deal = await deals_repo.get_deal(db_session, 5001)
    assert deal is not None
    assert deal.pipeline_id == 1
    assert deal.stage_id == 10
    assert deal.owner_id == 100
    assert deal.cf_project == "WTC Tower A"
    assert deal.cf_sqm_size == 120.5
    assert deal.custom_fields == {"cf_gross_rate": "5000"}
    assert "deal_sales_account_phone" not in deal.raw_payload

    events = await _events_for_deal(db_session, 5001)
    assert [e.event_type for e in events] == ["created"]


async def test_ingest_webhook_records_stage_change(db_session: AsyncSession) -> None:
    await _seed_reference_data(db_session)
    resolver = await build_stage_resolver(db_session)

    await ingest_webhook(db_session, _base_payload(), resolver)
    await ingest_webhook(
        db_session,
        _base_payload(deal_deal_stage_name="Won", deal_stage_updated_time="06-16-2026 09:00:00"),
        resolver,
    )

    deal = await deals_repo.get_deal(db_session, 5001)
    assert deal is not None
    assert deal.stage_id == 11

    events = await _events_for_deal(db_session, 5001)
    assert [e.event_type for e in events] == ["created", "stage_change"]
    assert events[1].old_stage_id == 10
    assert events[1].new_stage_id == 11
