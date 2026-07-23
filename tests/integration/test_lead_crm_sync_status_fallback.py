"""Freshsales rejects contact_status_id for some contacts with a 400 ("Invalid
contact status provided") for reasons that don't correlate with our payload —
observed live on both a brand-new and a years-old pre-existing contact. It's an
optional field; sync_lead must retry without it rather than losing the whole
contact sync over it. Driven over a mocked Freshsales API (respx)."""

import json

import httpx
import respx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.freshsales.client import FreshsalesClient
from app.models.campaign import STATUS_ACTIVE, Campaign
from app.models.lead import CRM_FAILED, CRM_SYNCED
from app.repositories import campaigns_repo
from app.schemas.campaigns import LeadCreateRequest
from app.services import lead_crm_sync, lead_service


async def _website_lead(session: AsyncSession, email: str):
    campaign = await campaigns_repo.create(
        session, Campaign(slug="wtcabuja-website", name="Website", status=STATUS_ACTIVE, config={})
    )
    lead = await lead_service.capture_lead(
        session, campaign.slug,
        LeadCreateRequest(
            first_name="Ada", last_name="Lovelace", email=email,
            phone="+2348012345678", company="Energy Co",
        ),
    )
    return lead, campaign


async def test_retries_without_contact_status_on_that_specific_400(
    db_session: AsyncSession,
) -> None:
    lead, campaign = await _website_lead(db_session, "status-conflict@example.com")
    enabled = Settings(freshsales_lead_sync_enabled=True, freshsales_api_key="SG.test")

    responses = [
        httpx.Response(
            400, json={"errors": {"code": 400, "message": ["Invalid contact status provided."]}}
        ),
        httpx.Response(200, json={"contact": {"id": 999}}),
    ]
    with respx.mock(base_url=enabled.freshsales_base_url) as router:
        route = router.post("/crm/sales/api/contacts/upsert").mock(side_effect=responses)
        async with FreshsalesClient(enabled) as client:
            result = await lead_crm_sync.sync_lead(
                db_session, lead, campaign, client=client, settings=enabled
            )

    assert route.call_count == 2
    # Second attempt must have dropped contact_status_id from the payload.
    second_body = json.loads(route.calls[1].request.content)
    assert "contact_status_id" not in second_body["contact"]
    assert result.crm_sync_status == CRM_SYNCED
    assert result.crm_contact_id == "999"


async def test_does_not_retry_on_an_unrelated_400(db_session: AsyncSession) -> None:
    lead, campaign = await _website_lead(db_session, "other-error@example.com")
    enabled = Settings(freshsales_lead_sync_enabled=True, freshsales_api_key="SG.test")

    with respx.mock(base_url=enabled.freshsales_base_url) as router:
        route = router.post("/crm/sales/api/contacts/upsert").mock(
            return_value=httpx.Response(
                400, json={"errors": {"code": 400, "message": ["Some other validation error."]}}
            )
        )
        async with FreshsalesClient(enabled) as client:
            result = await lead_crm_sync.sync_lead(
                db_session, lead, campaign, client=client, settings=enabled
            )

    assert route.call_count == 1
    assert result.crm_sync_status == CRM_FAILED
    assert "Some other validation error" in result.crm_error
