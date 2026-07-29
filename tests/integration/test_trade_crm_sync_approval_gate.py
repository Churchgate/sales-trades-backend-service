"""Programs opting into config["require_admin_approval"] must never sync a
participant to Freshsales until an admin has approved them (see
trade_repo.set_review / PATCH /trade/participants/{id}/review) — regression
coverage for the new approval gate in sync_trade_lead, same
skip-not-fail-forever contract as the no-email gate.
"""

import respx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.freshsales.client import FreshsalesClient
from app.models.trade_lead import CRM_PENDING, CRM_SKIPPED, REVIEW_APPROVED, TradeLead
from app.models.trade_program import STATUS_ACTIVE, TradeProgram
from app.repositories import trade_repo
from app.services import trade_crm_sync


async def _mission_program(session: AsyncSession) -> TradeProgram:
    return await trade_repo.create_program(
        session,
        TradeProgram(
            slug="canton-fair-2026",
            name="Canton Fair",
            status=STATUS_ACTIVE,
            config={"crm_sync_enabled": True, "require_admin_approval": True},
        ),
    )


async def test_pending_review_status_is_skipped_not_failed(db_session: AsyncSession) -> None:
    program = await _mission_program(db_session)
    lead = await trade_repo.create_lead(
        db_session,
        TradeLead(
            trade_program_id=program.id,
            registration_id="reg-1",
            participant_index=1,
            is_primary=True,
            first_name="Chidi",
            last_name="Okoro",
            email="chidi@example.com",
            crm_sync_status=CRM_PENDING,
        ),
    )
    enabled = Settings(freshsales_lead_sync_enabled=True, freshsales_api_key="SG.test")

    with respx.mock(assert_all_called=True) as router:
        # No route registered for contacts/upsert — asserting nothing calls it.
        async with FreshsalesClient(enabled) as client:
            result = await trade_crm_sync.sync_trade_lead(
                db_session, lead, program, client=client, settings=enabled
            )
        assert router.calls.call_count == 0

    assert result.crm_sync_status == CRM_SKIPPED


async def test_approved_review_status_proceeds_to_sync(db_session: AsyncSession) -> None:
    program = await _mission_program(db_session)
    lead = await trade_repo.create_lead(
        db_session,
        TradeLead(
            trade_program_id=program.id,
            registration_id="reg-2",
            participant_index=1,
            is_primary=True,
            first_name="Ada",
            last_name="Nwosu",
            email="ada@example.com",
            crm_sync_status=CRM_PENDING,
            review_status=REVIEW_APPROVED,
        ),
    )
    enabled = Settings(freshsales_lead_sync_enabled=True, freshsales_api_key="SG.test")

    with respx.mock(assert_all_called=True) as router:
        router.post("https://rbpropertieslimited.myfreshworks.com/crm/sales/api/contacts/upsert").respond(
            200, json={"contact": {"id": 42}}
        )
        async with FreshsalesClient(enabled) as client:
            result = await trade_crm_sync.sync_trade_lead(
                db_session, lead, program, client=client, settings=enabled
            )
        assert router.calls.call_count == 1

    assert result.crm_contact_id == "42"
