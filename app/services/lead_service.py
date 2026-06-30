"""Lead capture orchestration for the booth/stand app.

Capture's one job is to never lose a lead: validate minimally, persist
immediately, and dedup by email within the campaign. The lead is saved with
`crm_sync_status='pending'`; the scheduled `lead_crm_sync_job` pushes it to
Freshsales later, so capture never blocks on (or fails because of) the network.
"""

from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.campaign import STATUS_ACTIVE, Campaign
from app.models.lead import CRM_PENDING, PACK_NOT_REQUESTED, PACK_PENDING, Lead
from app.repositories import campaigns_repo, leads_repo
from app.schemas.campaigns import LeadCreateRequest

logger = get_logger(__name__)


class LeadCaptureError(Exception):
    """Base for lead-capture domain errors."""


class CampaignNotFoundError(LeadCaptureError):
    pass


class CampaignInactiveError(LeadCaptureError):
    pass


def _derive_tags(campaign: Campaign, payload: LeadCreateRequest) -> list[str]:
    """CRM tags from the campaign's (dynamic) config + this lead's selections."""
    config = campaign.config or {}
    tags: list[str] = list(config.get("base_tags", []))
    tag_map: dict[str, str] = config.get("tag_map", {})
    for interest in payload.interests or []:
        tags.append(tag_map.get(interest, interest))
    if payload.requested_materials:
        tags.append(config.get("digital_pack_tag", "Digital Pack"))
    if payload.inspection_requested:
        tags.append(config.get("inspection_tag", "Private Inspection"))
    if payload.marketing_opt_in:
        tags.append(config.get("newsletter_tag", "Newsletter Opt-In"))
    # De-dup, preserve order.
    seen: set[str] = set()
    return [t for t in tags if t and not (t in seen or seen.add(t))]


def _apply_payload(lead: Lead, campaign: Campaign, payload: LeadCreateRequest) -> None:
    lead.first_name = payload.first_name
    lead.last_name = payload.last_name
    lead.phone = payload.phone
    lead.company = payload.company
    lead.job_title = payload.job_title
    lead.source = payload.source
    lead.device_id = payload.device_id
    lead.timing = payload.timing
    lead.interests = payload.interests
    lead.requested_materials = payload.requested_materials
    lead.tags = _derive_tags(campaign, payload)
    lead.inspection_requested = payload.inspection_requested
    lead.inspection_type = payload.inspection_type
    lead.marketing_opt_in = payload.marketing_opt_in
    lead.consent_status = payload.consent_status
    lead.captured_at = payload.captured_at
    lead.responses = payload.responses or {}
    if payload.consent_status and lead.consent_at is None:
        lead.consent_at = payload.captured_at or datetime.now(UTC)
    # New/updated info must be (re)pushed to the CRM.
    lead.crm_sync_status = CRM_PENDING
    lead.crm_error = None
    # Requested materials must be (re)delivered. The delivery service decides what
    # is actually deliverable (real materials with a configured asset); here we
    # just flag that there's something to attempt vs nothing at all.
    lead.pack_delivery_status = (
        PACK_PENDING if payload.requested_materials else PACK_NOT_REQUESTED
    )
    lead.pack_delivery_error = None


async def capture_lead(
    session: AsyncSession, slug: str, payload: LeadCreateRequest
) -> Lead:
    """Create (or dedup-merge) a lead for the active campaign `slug`.

    Dedup is by (campaign_id, email) — this is the idempotency mechanism: an
    offline-queue retry of the same submission lands on the same row instead
    of creating a duplicate. The SELECT-then-INSERT below has a race window
    (two retries fired close together can both miss the SELECT), so a unique
    violation on INSERT is treated the same as finding the row up front —
    re-fetch and merge instead of erroring the request.
    """
    campaign = await campaigns_repo.get_by_slug(session, slug)
    if campaign is None:
        raise CampaignNotFoundError(f"no campaign with slug {slug}")
    if campaign.status != STATUS_ACTIVE:
        raise CampaignInactiveError(f"campaign {slug} is not accepting leads")

    campaign_id = campaign.id
    email = str(payload.email).strip().lower()
    existing = await leads_repo.get_by_campaign_email(session, campaign_id, email)
    lead = existing or Lead(campaign_id=campaign_id, email=email)
    _apply_payload(lead, campaign, payload)

    if existing is not None:
        return await leads_repo.update(session, lead)

    try:
        return await leads_repo.create(session, lead)
    except IntegrityError:
        # session.rollback() expires already-loaded instances (incl. `campaign`),
        # so re-fetch both by plain id rather than touching the stale objects.
        await session.rollback()
        existing = await leads_repo.get_by_campaign_email(session, campaign_id, email)
        if existing is None:
            raise
        campaign = await campaigns_repo.get(session, campaign_id)
        _apply_payload(existing, campaign, payload)
        return await leads_repo.update(session, existing)
