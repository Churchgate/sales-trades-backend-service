"""lead_triage_sync: pulling Freshsales' own `last_contacted` signal back into
our `triage_status`, driven over a mocked Freshsales API (respx)."""

import httpx
import respx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.freshsales.client import FreshsalesClient
from app.models.campaign import STATUS_ACTIVE, Campaign
from app.models.lead import TRIAGE_CONTACTED, TRIAGE_NEW, Lead
from app.repositories import campaigns_repo
from app.schemas.campaigns import LeadCreateRequest
from app.services import lead_service
from app.services.lead_triage_sync import SYSTEM_TRIAGE_BY, sync_triage_from_crm

BASE_URL = "https://rbpropertieslimited.myfreshworks.com"


async def _make_lead(session: AsyncSession, email: str, *, crm_contact_id: str | None) -> Lead:
    campaign = await campaigns_repo.create(
        session, Campaign(slug=f"c-{email}", name="C", status=STATUS_ACTIVE, config={})
    )
    lead = await lead_service.capture_lead(
        session, campaign.slug,
        LeadCreateRequest(
            first_name="Ada", last_name="Lovelace", email=email,
            phone="+2348012345678", company="Energy Co",
        ),
    )
    lead.crm_contact_id = crm_contact_id
    session.add(lead)
    await session.commit()
    await session.refresh(lead)
    return lead


async def test_advances_triage_when_freshsales_shows_last_contacted(
    db_session: AsyncSession,
) -> None:
    lead = await _make_lead(db_session, "contacted@example.com", crm_contact_id="501")

    with respx.mock(base_url=BASE_URL, assert_all_called=True) as router:
        router.get(url__regex=r".*/crm/sales/api/contacts/501.*").mock(
            return_value=httpx.Response(
                200, json={"contact": {"id": 501, "last_contacted": "2026-07-20T10:00:00Z"}}
            )
        )
        async with FreshsalesClient() as client:
            advanced = await sync_triage_from_crm(db_session, client)

    assert advanced == 1
    await db_session.refresh(lead)
    assert lead.triage_status == TRIAGE_CONTACTED
    assert lead.triage_by == SYSTEM_TRIAGE_BY


async def test_leaves_triage_alone_when_freshsales_shows_no_contact(
    db_session: AsyncSession,
) -> None:
    lead = await _make_lead(db_session, "untouched@example.com", crm_contact_id="502")

    with respx.mock(base_url=BASE_URL, assert_all_called=True) as router:
        router.get(url__regex=r".*/crm/sales/api/contacts/502.*").mock(
            return_value=httpx.Response(200, json={"contact": {"id": 502, "last_contacted": None}})
        )
        async with FreshsalesClient() as client:
            advanced = await sync_triage_from_crm(db_session, client)

    assert advanced == 0
    await db_session.refresh(lead)
    assert lead.triage_status == TRIAGE_NEW


async def test_skips_leads_without_a_crm_contact(db_session: AsyncSession) -> None:
    await _make_lead(db_session, "unsynced@example.com", crm_contact_id=None)

    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        async with FreshsalesClient() as client:
            advanced = await sync_triage_from_crm(db_session, client)

    assert advanced == 0
    assert len(router.calls) == 0


async def test_never_overrides_a_human_triage_decision(db_session: AsyncSession) -> None:
    """A lead a rep already dismissed via our own dashboard must stay dismissed,
    even if Freshsales later shows last_contacted — the CRM signal only ever
    advances a lead OUT of the default `new` state, never overrides one a human
    has already set."""
    from app.models.lead import TRIAGE_DISMISSED
    from app.repositories import leads_repo

    lead = await _make_lead(db_session, "dismissed@example.com", crm_contact_id="503")
    await leads_repo.set_triage(db_session, lead, status=TRIAGE_DISMISSED, by="rep@example.com")

    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        async with FreshsalesClient() as client:
            advanced = await sync_triage_from_crm(db_session, client)

    assert advanced == 0
    assert len(router.calls) == 0
    result = (await db_session.execute(select(Lead).where(Lead.id == lead.id))).scalar_one()
    assert result.triage_status == TRIAGE_DISMISSED
