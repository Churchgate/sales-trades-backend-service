"""Booth/stand lead-capture API (WTC Abuja Interactive Stand App).

Public, unauthenticated capture (the booth tablet + QR-to-phone form GET a
campaign's config and POST leads). Staff-only admin reads (list/stats/CSV/resync)
gated to admin/superadmin — the management 'how did the event go' surface.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from app.api.dependencies import SessionDep, require_role
from app.models.campaign import Campaign
from app.models.lead import PACK_PENDING, Lead
from app.repositories import campaigns_repo, leads_repo
from app.schemas.campaigns import (
    CampaignCreateRequest,
    CampaignDetailResponse,
    CampaignOut,
    CampaignsListResponse,
    CampaignStats,
    CampaignStatsResponse,
    CampaignUpdateRequest,
    DayCount,
    LeadCaptureResponse,
    LeadCreateRequest,
    LeadOut,
    LeadsListResponse,
)
from app.schemas.responses import MessageResponse
from app.services import (
    campaign_mailer,
    lead_crm_sync,
    lead_export,
    lead_scoring,
    lead_service,
    pack_delivery,
)

router = APIRouter(prefix="/campaigns", tags=["campaigns"])

_ADMIN_ROLES = ("admin", "superadmin")


def _campaign_out(campaign: Campaign) -> CampaignOut:
    return CampaignOut(
        id=campaign.id,
        slug=campaign.slug,
        name=campaign.name,
        status=campaign.status,
        starts_on=campaign.starts_on,
        ends_on=campaign.ends_on,
        timezone=campaign.timezone,
        config=campaign.config,
        created_at=campaign.created_at,
        updated_at=campaign.updated_at,
    )


def _lead_out(lead: Lead) -> LeadOut:
    out = LeadOut.model_validate(lead, from_attributes=True)
    out.pack_fulfilled = lead_scoring.pack_fulfilled(lead)
    out.engagement_score = lead_scoring.engagement_score(lead)
    return out


async def _require_campaign(session: SessionDep, campaign_id: int) -> Campaign:
    campaign = await campaigns_repo.get(session, campaign_id)
    if campaign is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Campaign not found")
    return campaign


# --- Admin: campaign management ---


@router.get("", dependencies=[Depends(require_role(*_ADMIN_ROLES))])
async def list_campaigns(session: SessionDep) -> CampaignsListResponse:
    campaigns = await campaigns_repo.list_all(session)
    return CampaignsListResponse(
        status_code=status.HTTP_200_OK,
        campaigns=[_campaign_out(c) for c in campaigns],
    )


@router.post(
    "",
    dependencies=[Depends(require_role(*_ADMIN_ROLES))],
    status_code=status.HTTP_201_CREATED,
)
async def create_campaign(
    body: CampaignCreateRequest, session: SessionDep
) -> CampaignDetailResponse:
    if await campaigns_repo.get_by_slug(session, body.slug) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Campaign slug already exists"
        )
    campaign = await campaigns_repo.create(session, Campaign(**body.model_dump()))
    return CampaignDetailResponse(
        status_code=status.HTTP_201_CREATED, campaign=_campaign_out(campaign)
    )


# --- Public: booth/QR config + capture ---


@router.get("/{slug}")
async def get_campaign(slug: str, session: SessionDep) -> CampaignDetailResponse:
    """Public — the booth app/QR form reads its (dynamic) config from here."""
    campaign = await campaigns_repo.get_by_slug(session, slug.strip())
    if campaign is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Campaign not found")
    return CampaignDetailResponse(
        status_code=status.HTTP_200_OK, campaign=_campaign_out(campaign)
    )


@router.post("/{slug}/leads", status_code=status.HTTP_201_CREATED)
async def capture_lead(
    slug: str, body: LeadCreateRequest, session: SessionDep
) -> LeadCaptureResponse:
    """Public — capture a visitor lead. Saved immediately; CRM push happens later.

    If the visitor requested a digital pack we attempt to email it inline so it
    arrives 'right away' (best-effort — `deliver_pack` never raises); the
    scheduled `pack_delivery_job` is the backstop for anything not yet sent.
    """
    try:
        lead = await lead_service.capture_lead(session, slug.strip(), body)
    except lead_service.CampaignNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except lead_service.CampaignInactiveError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    campaign = await campaigns_repo.get(session, lead.campaign_id)
    if campaign is not None:
        if lead.pack_delivery_status == PACK_PENDING:
            lead = await pack_delivery.deliver_pack(session, lead, campaign)
        if lead.inspection_requested:
            lead = await pack_delivery.deliver_viewing(session, lead, campaign)
        await campaign_mailer.send_lead_notification(lead, campaign)

    return LeadCaptureResponse(status_code=status.HTTP_201_CREATED, lead=_lead_out(lead))


# --- Admin: campaign editing + lead reads (by numeric id) ---


@router.patch("/{campaign_id}", dependencies=[Depends(require_role(*_ADMIN_ROLES))])
async def update_campaign(
    campaign_id: int, body: CampaignUpdateRequest, session: SessionDep
) -> CampaignDetailResponse:
    campaign = await _require_campaign(session, campaign_id)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(campaign, field, value)
    campaign = await campaigns_repo.update(session, campaign)
    return CampaignDetailResponse(
        status_code=status.HTTP_200_OK, campaign=_campaign_out(campaign)
    )


@router.get("/{campaign_id}/leads", dependencies=[Depends(require_role(*_ADMIN_ROLES))])
async def list_leads(
    campaign_id: int,
    session: SessionDep,
    interest: Annotated[str | None, Query()] = None,
    inspection: Annotated[bool | None, Query()] = None,
    opt_in: Annotated[bool | None, Query()] = None,
    sync_status: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> LeadsListResponse:
    await _require_campaign(session, campaign_id)
    leads = await leads_repo.list_for_campaign(
        session, campaign_id,
        interest=interest, inspection=inspection, opt_in=opt_in,
        sync_status=sync_status, limit=limit, offset=offset,
    )
    total = await leads_repo.count_for_campaign(
        session, campaign_id,
        interest=interest, inspection=inspection, opt_in=opt_in, sync_status=sync_status,
    )
    return LeadsListResponse(
        status_code=status.HTTP_200_OK, leads=[_lead_out(lead) for lead in leads], total=total
    )


@router.delete(
    "/{campaign_id}/leads/{lead_id}", dependencies=[Depends(require_role("superadmin"))]
)
async def delete_lead(campaign_id: int, lead_id: int, session: SessionDep) -> MessageResponse:
    """Permanently remove one captured lead — e.g. test/QA submissions, or a
    visitor's deletion request. Superadmin-only: this is destructive (unlike CRM
    sync or pack delivery, there's no retry/undo) and bypasses the CSV export
    system-of-record, so it's scoped tighter than the rest of the admin surface."""
    await _require_campaign(session, campaign_id)
    lead = await leads_repo.get(session, lead_id)
    if lead is None or lead.campaign_id != campaign_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found")
    await leads_repo.delete(session, lead)
    return MessageResponse(status_code=status.HTTP_200_OK, message="Lead deleted")


@router.post(
    "/{campaign_id}/leads/{lead_id}/resend-pack",
    dependencies=[Depends(require_role(*_ADMIN_ROLES))],
)
async def resend_lead_pack(
    campaign_id: int, lead_id: int, session: SessionDep
) -> LeadCaptureResponse:
    """Re-attempt digital-pack delivery for a single lead — the per-lead version of
    the bulk resend, for when staff want to retry one visitor from the dashboard.
    `deliver_pack` recomputes the deliverable materials and re-sends regardless of
    the current status, so it works whether the pack previously failed, was skipped,
    or already sent."""
    campaign = await _require_campaign(session, campaign_id)
    lead = await leads_repo.get(session, lead_id)
    if lead is None or lead.campaign_id != campaign_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found")
    lead = await pack_delivery.deliver_pack(session, lead, campaign)
    return LeadCaptureResponse(status_code=status.HTTP_200_OK, lead=_lead_out(lead))


@router.delete("/{campaign_id}/leads", dependencies=[Depends(require_role("superadmin"))])
async def bulk_delete_leads(
    campaign_id: int,
    session: SessionDep,
    confirm: Annotated[bool, Query()] = False,
    interest: Annotated[str | None, Query()] = None,
    inspection: Annotated[bool | None, Query()] = None,
    opt_in: Annotated[bool | None, Query()] = None,
    sync_status: Annotated[str | None, Query()] = None,
) -> MessageResponse:
    """Bulk-remove leads matching the given filters — the QA/test-campaign purge
    case (e.g. clearing a kiosk-app-qa campaign between test runs). Same filters
    as GET .../leads; omit all of them to delete every lead in the campaign.

    `confirm=true` is required on every call (not just a role check) — this is
    the single most destructive endpoint in the API, with no filter applied by
    default it wipes the whole campaign's leads, and unlike CRM sync or pack
    delivery there's no retry/undo."""
    await _require_campaign(session, campaign_id)
    if not confirm:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Pass confirm=true to bulk-delete leads (no undo).",
        )
    deleted = await leads_repo.delete_for_campaign(
        session, campaign_id,
        interest=interest, inspection=inspection, opt_in=opt_in, sync_status=sync_status,
    )
    return MessageResponse(status_code=status.HTTP_200_OK, message=f"Deleted {deleted} lead(s)")


@router.get("/{campaign_id}/stats", dependencies=[Depends(require_role(*_ADMIN_ROLES))])
async def campaign_stats(campaign_id: int, session: SessionDep) -> CampaignStatsResponse:
    campaign = await _require_campaign(session, campaign_id)
    total = await leads_repo.count_for_campaign(session, campaign_id)
    synced = await leads_repo.count_synced(session, campaign_id)
    by_day = await leads_repo.counts_by_day(session, campaign_id, campaign.timezone)
    stats = CampaignStats(
        total_leads=total,
        inspection_requests=await leads_repo.count_inspection_requests(session, campaign_id),
        marketing_opt_ins=await leads_repo.count_opt_ins(session, campaign_id),
        synced_count=synced,
        unsynced_count=total - synced,
        packs_delivered=await leads_repo.count_packs_delivered(session, campaign_id),
        by_interest=await leads_repo.counts_by_interest(session, campaign_id),
        by_material=await leads_repo.counts_by_material(session, campaign_id),
        by_source=await leads_repo.counts_by_source(session, campaign_id),
        by_day=[DayCount(day=day, count=count) for day, count in by_day],
    )
    return CampaignStatsResponse(status_code=status.HTTP_200_OK, stats=stats)


@router.get(
    "/{campaign_id}/leads/export.csv",
    dependencies=[Depends(require_role(*_ADMIN_ROLES))],
)
async def export_leads_csv(campaign_id: int, session: SessionDep) -> Response:
    campaign = await _require_campaign(session, campaign_id)
    leads = await leads_repo.list_for_campaign(session, campaign_id, limit=100_000)
    csv_text = lead_export.leads_to_csv(leads)
    filename = f"{campaign.slug}-leads.csv"
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/{campaign_id}/resync", dependencies=[Depends(require_role(*_ADMIN_ROLES))])
async def resync_leads(campaign_id: int, session: SessionDep) -> CampaignStatsResponse:
    """Re-attempt CRM sync for pending/failed leads, then return fresh stats."""
    await _require_campaign(session, campaign_id)
    await lead_crm_sync.sync_pending(session)
    return await campaign_stats(campaign_id, session)


@router.post("/{campaign_id}/resend-packs", dependencies=[Depends(require_role(*_ADMIN_ROLES))])
async def resend_packs(campaign_id: int, session: SessionDep) -> CampaignStatsResponse:
    """Re-attempt digital-pack email for pending/failed leads, then return stats."""
    await _require_campaign(session, campaign_id)
    await pack_delivery.deliver_pending(session)
    return await campaign_stats(campaign_id, session)
