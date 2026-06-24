"""Push captured leads into Freshsales as contacts (best-effort, deduped).

Lead capture never depends on this — a lead is saved `pending` and pushed here,
either inline (best-effort) or by the scheduled `lead_crm_sync_job`. Live sync is
gated by `settings.freshsales_lead_sync_enabled`; when off, leads are marked
`skipped` and the CSV export is the system of record (brief §23).

The exact Freshsales contact field names (company, custom fields) need live
verification before enabling; until then this is exercised via mocked tests.
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.freshsales.client import FreshsalesClient
from app.models.campaign import Campaign
from app.models.lead import CRM_FAILED, CRM_SKIPPED, CRM_SYNCED, Lead
from app.repositories import campaigns_repo, leads_repo

logger = get_logger(__name__)


def build_contact_payload(lead: Lead, campaign: Campaign) -> dict[str, Any]:
    """Map a Lead to a Freshsales contacts/upsert body (deduped by email)."""
    contact: dict[str, Any] = {
        "first_name": lead.first_name,
        "last_name": lead.last_name,
        "emails": [{"value": lead.email, "is_primary": True}],
        "mobile_number": lead.phone,
        "job_title": lead.job_title,
        "tags": lead.tags or [],
        "custom_field": {
            "cf_company": lead.company,
            "cf_campaign": campaign.slug,
            "cf_interests": ", ".join(lead.interests or []),
            "cf_requested_materials": ", ".join(lead.requested_materials or []),
            "cf_timing": lead.timing,
            "cf_inspection_requested": lead.inspection_requested,
            "cf_marketing_opt_in": lead.marketing_opt_in,
            "cf_consent": lead.consent_status,
            "cf_source": lead.source,
        },
    }
    return {"unique_identifier": {"emails": lead.email}, "contact": contact}


async def sync_lead(
    session: AsyncSession,
    lead: Lead,
    campaign: Campaign,
    *,
    client: FreshsalesClient,
    settings: Settings | None = None,
) -> Lead:
    """Push one lead to Freshsales, recording the outcome on the lead. Never raises."""
    settings = settings or get_settings()
    if not settings.freshsales_lead_sync_enabled:
        lead.crm_sync_status = CRM_SKIPPED
        return await leads_repo.update(session, lead)

    try:
        contact = await client.upsert_contact(build_contact_payload(lead, campaign))
        contact_id = contact.get("id")
        lead.crm_contact_id = str(contact_id) if contact_id is not None else None
        lead.crm_sync_status = CRM_SYNCED
        lead.crm_synced_at = datetime.now(UTC)
        lead.crm_error = None
    except Exception as exc:  # noqa: BLE001 — record and move on; job retries later
        logger.warning("lead crm sync failed", lead_id=lead.id, error=str(exc))
        lead.crm_sync_status = CRM_FAILED
        lead.crm_error = str(exc)[:500]
    return await leads_repo.update(session, lead)


async def sync_pending(session: AsyncSession, *, limit: int = 200) -> int:
    """Push all pending/failed leads (across campaigns). Returns #synced this run."""
    settings = get_settings()
    if not settings.freshsales_lead_sync_enabled:
        return 0

    leads = await leads_repo.list_pending_crm_sync(
        session, statuses=[CRM_FAILED, "pending"], limit=limit
    )
    if not leads:
        return 0

    campaigns: dict[int, Campaign] = {}
    synced = 0
    async with FreshsalesClient(settings) as client:
        for lead in leads:
            campaign = campaigns.get(lead.campaign_id)
            if campaign is None:
                campaign = await campaigns_repo.get(session, lead.campaign_id)
                if campaign is None:
                    continue
                campaigns[lead.campaign_id] = campaign
            result = await sync_lead(
                session, lead, campaign, client=client, settings=settings
            )
            if result.crm_sync_status == CRM_SYNCED:
                synced += 1
    return synced
