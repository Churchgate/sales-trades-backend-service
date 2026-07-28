"""lead_prospecting.ingest_company_signal — the Railway side of the rebuilt AI Lead
Generation Engine. n8n's `Web scan` sources+enriches a company and POSTs it here;
this module fetches senior contacts (via 03_Add POC per company's webhook), dedups,
scores with the real ICP rubric, and persists — all driven over mocked HTTP (respx)."""

import json

import httpx
import respx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.campaign import STATUS_ACTIVE, Campaign
from app.models.lead import Lead
from app.repositories import campaigns_repo
from app.services.lead_prospecting import ProspectCompanySignal, ingest_company_signal

N8N_WEBHOOK_URL = "https://n8n.example.com/webhook/find-poc"

_ORGANIZATION = {
    "name": "Zedcrest Group",
    "primary_domain": "zedcrest.com",
    "industry": "financial services",
    "estimated_num_employees": 550,
    "country": "Nigeria",
    "city": "Lagos",
    "founded_year": 2013,
    "short_description": "A Lagos-based financial services group.",
}

_CONTACT = {
    "first_name": "Ada",
    "last_name": "Lovelace",
    "email": "ada@zedcrest.com",
    "professional_title": "Chief Financial Officer",
    "linkedin_url": "https://linkedin.com/in/ada",
    "country": "Nigeria",
    "person_id": "abc123",
}

_ICP_RESPONSE = {
    "industry_score": 25,
    "financial_capacity_score": 18,
    "footprint_score": 30,
    "trigger_score": 8,
    "icp_score": 81,
    "lead_tier": "Tier 1",
    "trigger_event": "Acquisition of a cross-border payments startup",
    "rationale": "Established Nigerian financial services group with confirmed Lagos presence.",
}


def _settings(**overrides) -> Settings:
    data = {
        "n8n_poc_webhook_url": N8N_WEBHOOK_URL,
        "openrouter_api_key": "or-test",
        "freshsales_lead_sync_enabled": False,
        "freshsales_api_key": "fs-test",
    }
    data.update(overrides)
    return Settings(**data)


async def _seed_campaign(session: AsyncSession) -> Campaign:
    return await campaigns_repo.create(
        session,
        Campaign(
            slug="ai-prospecting", name="AI Lead Generation Engine",
            status=STATUS_ACTIVE, config={"crm_sync_enabled": False},
        ),
    )


def _mock_poc_webhook(router: respx.MockRouter, contacts: list[dict]) -> None:
    router.post(N8N_WEBHOOK_URL).mock(
        return_value=httpx.Response(200, json={"contacts": contacts})
    )


def _mock_openrouter(router: respx.MockRouter) -> None:
    router.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(
            200, json={"choices": [{"message": {"content": json.dumps(_ICP_RESPONSE)}}]}
        )
    )


async def test_skips_signal_with_no_resolved_domain(db_session: AsyncSession) -> None:
    await _seed_campaign(db_session)
    signal = ProspectCompanySignal(organization={"name": "Mystery Co", "primary_domain": ""})

    with respx.mock(assert_all_called=False):
        result = await ingest_company_signal(db_session, signal, settings=_settings())

    assert result.domain is None
    assert result.created == 0


async def test_creates_and_scores_a_lead_from_a_real_signal(db_session: AsyncSession) -> None:
    await _seed_campaign(db_session)
    signal = ProspectCompanySignal(
        organization=_ORGANIZATION,
        trigger_event="Acquisition of a cross-border payments startup",
        trigger_type="M&A Activity",
        article_url="https://businessday.ng/example",
    )

    with respx.mock(assert_all_called=False) as router:
        _mock_poc_webhook(router, [_CONTACT])
        _mock_openrouter(router)
        result = await ingest_company_signal(db_session, signal, settings=_settings())

    assert result.received == 1
    assert result.created == 1
    assert result.deduped_out == 0
    assert len(result.lead_ids) == 1

    lead = (
        await db_session.execute(select(Lead).where(Lead.id == result.lead_ids[0]))
    ).scalar_one()
    assert lead.email == "ada@zedcrest.com"
    assert lead.source == "ai_prospecting"
    assert lead.icp_score == 81
    assert lead.icp_tier == "Tier 1"
    assert lead.responses["enrichment"]["primary_domain"] == "zedcrest.com"
    assert lead.responses["trigger_event"] == "Acquisition of a cross-border payments startup"


async def test_dedups_against_an_existing_lead_in_the_campaign(db_session: AsyncSession) -> None:
    await _seed_campaign(db_session)
    signal = ProspectCompanySignal(organization=_ORGANIZATION)

    with respx.mock(assert_all_called=False) as router:
        _mock_poc_webhook(router, [_CONTACT])
        _mock_openrouter(router)
        first = await ingest_company_signal(db_session, signal, settings=_settings())
        second = await ingest_company_signal(db_session, signal, settings=_settings())

    assert first.created == 1
    assert second.created == 0
    assert second.deduped_out == 1


async def test_dedups_against_an_existing_freshsales_contact(db_session: AsyncSession) -> None:
    await _seed_campaign(db_session)
    settings = _settings(freshsales_lead_sync_enabled=True)
    signal = ProspectCompanySignal(organization=_ORGANIZATION)

    with respx.mock(assert_all_called=False) as router:
        _mock_poc_webhook(router, [_CONTACT])
        router.get(url__regex=r".*/crm/sales/api/lookup.*").mock(
            # Verified live: Freshsales double-nests this — {"contacts": {"contacts": [...]}}.
            return_value=httpx.Response(200, json={"contacts": {"contacts": [{"id": 999}]}})
        )
        result = await ingest_company_signal(db_session, signal, settings=settings)

    assert result.created == 0
    assert result.deduped_out == 1


async def test_creates_when_freshsales_lookup_genuinely_finds_no_contact(
    db_session: AsyncSession,
) -> None:
    """The real "not found" shape Freshsales returns, verified live —
    double-nested with an empty inner list, not a bare empty list."""
    await _seed_campaign(db_session)
    settings = _settings(freshsales_lead_sync_enabled=True)
    signal = ProspectCompanySignal(organization=_ORGANIZATION)

    with respx.mock(assert_all_called=False) as router:
        _mock_poc_webhook(router, [_CONTACT])
        _mock_openrouter(router)
        router.get(url__regex=r".*/crm/sales/api/lookup.*").mock(
            return_value=httpx.Response(200, json={"contacts": {"contacts": []}})
        )
        result = await ingest_company_signal(db_session, signal, settings=settings)

    assert result.created == 1
    assert result.deduped_out == 0
