"""A 2nd participant's email is optional; without one, Freshsales has nothing
to dedupe/identify a contact by and the upsert always 400s. sync_trade_lead
must skip these rather than attempt-and-fail forever on every scheduled
retry — regression test for that gap (found live: registrations with a
nameless-email 2nd participant kept failing identically every sync run)."""

import respx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.freshsales.client import FreshsalesClient
from app.models.trade_lead import CRM_PENDING, CRM_SKIPPED, TradeLead
from app.models.trade_program import STATUS_ACTIVE, TradeProgram
from app.repositories import trade_repo
from app.services import trade_crm_sync


async def test_second_participant_without_email_is_skipped_not_failed(
    db_session: AsyncSession,
) -> None:
    program = await trade_repo.create_program(
        db_session,
        TradeProgram(
            slug="export-launchpad-2026",
            name="Export Launchpad",
            status=STATUS_ACTIVE,
            config={"crm_sync_enabled": True},
        ),
    )
    second = await trade_repo.create_lead(
        db_session,
        TradeLead(
            trade_program_id=program.id,
            registration_id="reg-1",
            participant_index=2,
            is_primary=False,
            first_name="Titi",
            last_name="Adams",
            email="",
            crm_sync_status=CRM_PENDING,
        ),
    )
    enabled = Settings(freshsales_lead_sync_enabled=True, freshsales_api_key="SG.test")

    with respx.mock(assert_all_called=True) as router:
        # No route registered for contacts/upsert — asserting nothing calls it.
        async with FreshsalesClient(enabled) as client:
            result = await trade_crm_sync.sync_trade_lead(
                db_session, second, program, client=client, settings=enabled
            )
        assert router.calls.call_count == 0

    assert result.crm_sync_status == CRM_SKIPPED
    assert result.crm_contact_id is None
