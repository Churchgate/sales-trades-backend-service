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

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.freshsales.client import FreshsalesClient
from app.models.campaign import Campaign
from app.models.lead import CRM_FAILED, CRM_SKIPPED, CRM_SYNCED, PACK_SENT, Lead
from app.repositories import campaigns_repo, leads_repo

logger = get_logger(__name__)

# NOG Energy Week is the only campaign with its own Freshsales "Lifecycle
# Stage-NOG Week" custom field, so that one stays scoped to it. "Source" and the
# system "Lifecycle stage" field, however, are REQUIRED on every contact as of
# ~2026-07-15 — Freshsales started rejecting creates missing either with 400
# "Source can't be empty"/"Lifecycle stage can't be empty" (see incident: 4
# wtcabuja-website leads, ids 533-536, silently failed from that date; nog-2026
# leads happened to already be synced by then so the gap wasn't visible there,
# but the next new NOG lead would have hit the same 400). Both dropdowns are
# system fields: Freshsales requires the numeric choice id, a plain string/label
# 400s. Verified live via GET /crm/sales/api/selector/lead_sources and
# GET /crm/sales/api/settings/contacts/fields (field name `lifecycle_stage_id`).
_NOG_2026_SLUG = "nog-2026"
_WEBSITE_SLUG = "wtcabuja-website"
_NOG_LEAD_SOURCE_ID = 17001007403
_WEBSITE_LEAD_SOURCE_ID = 17001006640  # "Website" choice
_LEAD_SOURCE_ID_BY_CAMPAIGN = {
    _NOG_2026_SLUG: _NOG_LEAD_SOURCE_ID,
}
# Every contact must carry a lifecycle stage; new captures start at "Inquiry
# Stage" (position 1 of the dropdown) regardless of campaign.
_INQUIRY_STAGE_ID = 18006136123
# Website leads also get the system "Status" field set to "New" (contact_status_id,
# choice id verified live via GET /crm/sales/api/settings/contacts/fields) — NOG
# leads use the NOG-specific Lifecycle Stage field below instead, so this stays
# scoped to the website campaign to avoid stepping on that.
_WEBSITE_CONTACT_STATUS_NEW_ID = 17000261833


def build_contact_payload(lead: Lead, campaign: Campaign) -> dict[str, Any]:
    """Map a Lead to a Freshsales contacts/upsert body (deduped by email)."""
    contact: dict[str, Any] = {
        "first_name": lead.first_name,
        "last_name": lead.last_name,
        "emails": [{"value": lead.email, "is_primary": True}],
        "mobile_number": lead.phone,
        "job_title": lead.job_title,
        "tags": lead.tags or [],
        # Required on every contact — see module comment above.
        "lead_source_id": _LEAD_SOURCE_ID_BY_CAMPAIGN.get(
            campaign.slug, _WEBSITE_LEAD_SOURCE_ID
        ),
        "lifecycle_stage_id": _INQUIRY_STAGE_ID,
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
            # Company-fit rating from scripts/score_leads_icp.py (null until that's
            # run for this lead) — real field, verified live via
            # GET /crm/sales/api/settings/contacts/fields (`cf_icp_score`, number).
            "cf_icp_score": lead.icp_score,
        },
    }
    if campaign.slug == _NOG_2026_SLUG:
        pack_sent = lead.pack_delivery_status == PACK_SENT
        contact["custom_field"]["cf_collateral_sent"] = "Yes" if pack_sent else "No"
        contact["custom_field"]["cf_lifecycle_stagenog_week"] = (
            "Nurturing" if pack_sent else "New"
        )
    if campaign.slug == _WEBSITE_SLUG:
        contact["contact_status_id"] = _WEBSITE_CONTACT_STATUS_NEW_ID
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
    except httpx.HTTPStatusError as exc:
        # response.raise_for_status() in FreshsalesClient discards the body, which
        # carries the actual validation message (e.g. field errors) — recover it
        # here so crm_error is diagnosable instead of a bare "400 Bad Request".
        detail = f"{exc} | body: {exc.response.text}"
        logger.warning("lead crm sync failed", lead_id=lead.id, error=detail)
        lead.crm_sync_status = CRM_FAILED
        lead.crm_error = detail[:500]
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
