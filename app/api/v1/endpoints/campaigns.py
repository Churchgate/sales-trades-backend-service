"""Booth/stand lead-capture API (WTC Abuja Interactive Stand App).

Public, unauthenticated capture (the booth tablet + QR-to-phone form GET a
campaign's config and POST leads). Staff-only admin reads (list/stats/CSV/resync)
gated to admin/superadmin — the management 'how did the event go' surface.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from app.api.dependencies import SessionDep, require_role
from app.models.campaign import Campaign
from app.models.lead import Lead
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
from app.services import lead_crm_sync, lead_export, lead_service

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
    return LeadOut.model_validate(lead, from_attributes=True)


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
    """Public — capture a visitor lead. Saved immediately; CRM push happens later."""
    try:
        lead = await lead_service.capture_lead(session, slug.strip(), body)
    except lead_service.CampaignNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except lead_service.CampaignInactiveError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
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
        by_interest=await leads_repo.counts_by_interest(session, campaign_id),
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
