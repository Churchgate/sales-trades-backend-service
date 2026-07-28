"""AI Lead Generation Engine — the Railway side of the rebuilt n8n pipeline.

n8n keeps doing what it already does well: `Web scan` scans a curated,
verified-source allow-list (Nigerian regulators, Africa business/energy trade
press) with its own agent, enriches the company via Apollo, and applies a cheap
inline prescore. This module is the one new thing added on Railway: given a
company signal, find senior contacts (via `03_Add POC per company`'s webhook,
which does the actual Apollo people-search), dedup them against both our own
leads and real Freshsales contacts, score them with our own ICP rubric (the
same one Hot Leads uses), and store them — WITHOUT auto-syncing to Freshsales.

That last part is deliberate: the `ai-prospecting` campaign's
`crm_sync_enabled=False` (see scripts/seed_campaigns.py) means
`lead_crm_sync.sync_lead` always marks these `skipped` on its own. A human
reviews them on the Lead Engine dashboard page and syncs individually when
ready — cold-sourced contacts shouldn't flow into the CRM unseen.
"""

from typing import Any

import httpx
from pydantic import BaseModel, EmailStr
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.freshsales.client import FreshsalesClient
from app.repositories import campaigns_repo, leads_repo
from app.schemas.campaigns import LeadCreateRequest
from app.services import icp_scoring, lead_service

logger = get_logger(__name__)

PROSPECTING_CAMPAIGN_SLUG = "ai-prospecting"


class ProspectCompanySignal(BaseModel):
    """Body `Web scan`'s `Add lead to database` node already sends today —
    unchanged from the live n8n payload, just repointed here instead of
    SmartSuite. `organization` is the full Apollo company-enrich blob (same
    shape scripts/enrich_leads.py writes to `Lead.responses['enrichment']`)."""

    organization: dict[str, Any]
    trigger_event: str | None = None
    trigger_type: str | None = None
    article_url: str | None = None
    industry_sector: str | None = None
    # n8n's own inline prescore — informational only; icp_scoring.score_lead below
    # is the authoritative score once a lead is actually created.
    lead_tier: str | None = None
    icp_score: int | None = None


class ProspectContact(BaseModel):
    """One senior contact, as returned by `03_Add POC per company`'s webhook —
    field names match its existing `Add Contact to database2` mapping verbatim."""

    first_name: str
    last_name: str
    email: EmailStr
    professional_title: str | None = None
    linkedin_url: str | None = None
    country: str | None = None
    person_id: str | None = None
    email_1_subject: str | None = None
    email_1_body: str | None = None
    email_2_subject: str | None = None
    email_2_body: str | None = None
    email_3_subject: str | None = None
    email_3_body: str | None = None
    email_4_subject: str | None = None
    email_4_body: str | None = None


class ProspectIngestResult(BaseModel):
    domain: str | None = None
    received: int = 0
    deduped_out: int = 0
    created: int = 0
    lead_ids: list[int] = []


async def _fetch_contacts(domain: str, settings: Settings) -> list[ProspectContact]:
    """Ask n8n's `03_Add POC per company` (Apollo people-search + outreach
    drafting, unchanged) for senior contacts at this domain. Missing config or a
    malformed entry degrades to fewer contacts, never an error — one bad company
    signal must never block the rest of a batch."""
    if not settings.n8n_poc_webhook_url:
        logger.warning("prospecting: n8n_poc_webhook_url not configured, skipping")
        return []
    async with httpx.AsyncClient(timeout=300.0) as client:
        resp = await client.post(settings.n8n_poc_webhook_url, json={"domain": domain})
        resp.raise_for_status()
        data = resp.json()

    raw_contacts = data if isinstance(data, list) else data.get("contacts", [])
    contacts: list[ProspectContact] = []
    for raw in raw_contacts:
        try:
            contacts.append(ProspectContact(**raw))
        except Exception as exc:  # noqa: BLE001 — skip malformed entries, keep going
            logger.warning("prospecting: skipping malformed contact", error=str(exc))
    return contacts


async def _is_known_to_crm(email: str, settings: Settings) -> bool:
    if not settings.freshsales_lead_sync_enabled:
        return False
    async with FreshsalesClient(settings) as client:
        contact = await client.lookup_contact_by_email(email)
    return contact is not None


async def ingest_company_signal(
    session: AsyncSession, signal: ProspectCompanySignal, *, settings: Settings | None = None
) -> ProspectIngestResult:
    """Entry point for the `/webhooks/prospecting/companies` endpoint."""
    settings = settings or get_settings()
    domain = (signal.organization.get("primary_domain") or "").strip().lower()
    if not domain:
        # The real fix for the old pipeline's bug #1: a signal with no resolved
        # domain is skipped here, provably, rather than proceeding with a blank
        # value that used to crash a downstream required-field write.
        logger.info(
            "prospecting: skipping signal with no resolved domain",
            company=signal.organization.get("name"),
        )
        return ProspectIngestResult(domain=None)

    campaign = await campaigns_repo.get_by_slug(session, PROSPECTING_CAMPAIGN_SLUG)
    if campaign is None:
        logger.error(f"prospecting: {PROSPECTING_CAMPAIGN_SLUG} campaign not seeded")
        return ProspectIngestResult(domain=domain)

    contacts = await _fetch_contacts(domain, settings)
    result = ProspectIngestResult(domain=domain, received=len(contacts))

    async with httpx.AsyncClient() as icp_client:
        for contact in contacts:
            email = contact.email.strip().lower()

            existing = await leads_repo.get_by_campaign_email(session, campaign.id, email)
            if existing is not None or await _is_known_to_crm(email, settings):
                result.deduped_out += 1
                continue

            payload = LeadCreateRequest(
                first_name=contact.first_name,
                last_name=contact.last_name,
                email=email,
                company=signal.organization.get("name") or "Unknown",
                job_title=contact.professional_title,
                source="ai_prospecting",
                responses={
                    "enrichment": signal.organization,
                    "trigger_event": signal.trigger_event,
                    "trigger_type": signal.trigger_type,
                    "article_url": signal.article_url,
                    "industry_sector": signal.industry_sector,
                    "n8n_prescore": signal.icp_score,
                    "n8n_prescore_tier": signal.lead_tier,
                    "linkedin_url": contact.linkedin_url,
                    "country": contact.country,
                    "drafted_outreach": [
                        {"subject": contact.email_1_subject, "body": contact.email_1_body},
                        {"subject": contact.email_2_subject, "body": contact.email_2_body},
                        {"subject": contact.email_3_subject, "body": contact.email_3_body},
                        {"subject": contact.email_4_subject, "body": contact.email_4_body},
                    ],
                },
            )
            lead = await lead_service.capture_lead(session, PROSPECTING_CAMPAIGN_SLUG, payload)

            try:
                score = await icp_scoring.score_lead(
                    icp_client, settings.openrouter_api_key, lead
                )
                lead.icp_score = score.icp_score
                lead.icp_tier = score.lead_tier
                lead.icp_rationale = score.rationale
                await leads_repo.update(session, lead)
            except Exception as exc:  # noqa: BLE001 — lead is still created, just unscored
                logger.warning("prospecting: ICP scoring failed", lead_id=lead.id, error=str(exc))

            result.created += 1
            result.lead_ids.append(lead.id)

    return result
