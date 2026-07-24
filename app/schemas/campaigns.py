from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.models.lead import TRIAGE_CONTACTED, TRIAGE_DISMISSED, TRIAGE_NEW, TRIAGE_SNOOZED
from app.schemas.responses import BaseResponse

_TRIAGE_STATUSES = {TRIAGE_NEW, TRIAGE_CONTACTED, TRIAGE_DISMISSED, TRIAGE_SNOOZED}

# --- Requests ---


class CampaignCreateRequest(BaseModel):
    slug: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=200)
    status: str = "draft"
    starts_on: date | None = None
    ends_on: date | None = None
    timezone: str = "Africa/Lagos"
    config: dict[str, Any] = Field(default_factory=dict)


class CampaignUpdateRequest(BaseModel):
    """PATCH — every field optional; only provided fields are applied."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    status: str | None = None
    starts_on: date | None = None
    ends_on: date | None = None
    timezone: str | None = None
    config: dict[str, Any] | None = None


class TriageUpdateRequest(BaseModel):
    """PATCH — sales triage state on the Hot Leads queue. Independent of
    crm_sync_status/pack_delivery_status, which track system delivery, not
    whether a human has actually followed up."""

    status: str

    @field_validator("status")
    @classmethod
    def must_be_valid_triage_status(cls, v: str) -> str:
        if v not in _TRIAGE_STATUSES:
            raise ValueError(f"status must be one of: {', '.join(sorted(_TRIAGE_STATUSES))}")
        return v


class LeadCreateRequest(BaseModel):
    first_name: str = Field(min_length=1, max_length=120)
    last_name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    # Optional: the public website marks phone ("Mobile / WhatsApp") optional, so
    # blank submissions arrive as "". Accept None/"" here (stored as "") — requiring
    # a non-empty phone silently 422'd every phone-less registration.
    phone: str | None = Field(default=None, max_length=40)
    company: str = Field(min_length=1, max_length=200)
    job_title: str | None = Field(default=None, max_length=200)

    source: str = "qr"
    device_id: str | None = None
    timing: str | None = None

    interests: list[str] | None = None
    requested_materials: list[str] | None = None
    inspection_requested: bool = False
    inspection_type: str | None = None
    marketing_opt_in: bool = False
    consent_status: bool = False
    captured_at: datetime | None = None

    # Anything else the (per-event, dynamic) form collected — kept verbatim.
    responses: dict[str, Any] = Field(default_factory=dict)


# --- Read models ---


class CampaignOut(BaseModel):
    id: int
    slug: str
    name: str
    status: str
    starts_on: date | None = None
    ends_on: date | None = None
    timezone: str
    config: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class LeadOut(BaseModel):
    id: int
    campaign_id: int
    first_name: str
    last_name: str
    email: EmailStr
    phone: str
    company: str
    job_title: str | None = None
    source: str
    device_id: str | None = None
    timing: str | None = None
    interests: list[str] | None = None
    requested_materials: list[str] | None = None
    tags: list[str] | None = None
    # Anything the form collected beyond the typed columns above, kept verbatim
    # (see models/lead.py) — e.g. Export Launchpad's sector/financials/ownership
    # fields. Exposed here so the dashboard can render it (LeadDetailDialog).
    responses: dict[str, Any] = Field(default_factory=dict)
    inspection_requested: bool
    inspection_type: str | None = None
    marketing_opt_in: bool
    consent_status: bool
    consent_at: datetime | None = None
    captured_at: datetime | None = None
    created_at: datetime
    crm_sync_status: str
    crm_synced_at: datetime | None = None
    crm_contact_id: str | None = None
    pack_delivery_status: str
    pack_delivery_error: str | None = None
    pack_delivered_at: datetime | None = None
    pack_delivered_materials: list[str] | None = None
    # Engagement (SendGrid Event Webhook — services/email_event_ingest.py).
    pack_opened_at: datetime | None = None
    pack_opened_count: int = 0
    pack_clicked_materials: list[str] | None = None
    # Did they request a pack and did we deliver it? (dashboard request→delivery check)
    pack_fulfilled: bool = False
    # 0–100 intent score for ranking leads (see services/lead_scoring.py).
    engagement_score: int = 0
    # Company/individual fit score (see services/icp_scoring.py) — scored once
    # via scripts/score_leads_icp.py, not recomputed per request.
    icp_score: int | None = None
    icp_tier: str | None = None
    icp_rationale: str | None = None
    # Sales triage state on the Hot Leads queue — independent of the two
    # system-delivery fields above.
    triage_status: str = "new"
    triage_at: datetime | None = None
    triage_by: str | None = None


class DayCount(BaseModel):
    day: date
    count: int


class CampaignStats(BaseModel):
    total_leads: int
    inspection_requests: int
    marketing_opt_ins: int
    synced_count: int
    unsynced_count: int
    packs_delivered: int
    # Outbound email volume. `emails_sent` is the running total of every email sent
    # to this campaign's leads; `emails_by_kind` breaks it out (packs/viewing
    # confirmations vs the post-event reconnect broadcast). Someone who received a
    # pack AND the reconnect counts once in each kind.
    emails_sent: int
    emails_by_kind: dict[str, int]
    by_interest: dict[str, int]
    by_material: dict[str, int]
    by_source: dict[str, int]
    by_day: list[DayCount]
    # NOG-week lifecycle stage, mirroring the CRM field lead_crm_sync sets: a lead is
    # "Nurturing" once its digital pack is delivered, else "New".
    by_lifecycle_stage: dict[str, int]


class ActivityOwnerSummary(BaseModel):
    """Per-salesperson activity totals over the requested window."""

    owner_name: str  # rep name, or "Unassigned"
    call: int = 0
    email: int = 0
    meeting: int = 0
    note: int = 0
    total: int = 0


class ActivityRow(BaseModel):
    """One activity for the drill-down list."""

    activity_type: str  # call | email | meeting | note
    contact_name: str | None = None
    owner_name: str | None = None
    prospect_tier: str | None = None
    direction: str | None = None
    occurred_at: datetime
    subject: str | None = None


class CampaignActivities(BaseModel):
    """NOG Activities page payload: per-rep summary, filter options, and a capped
    drill-down list — all for the requested date range / owner / tier."""

    summary: list[ActivityOwnerSummary]
    owners: list[str]  # filter options (owners seen, incl. "Unassigned")
    tiers: list[str]  # ["Strategic", "Standard"]
    rows: list[ActivityRow]
    total: int  # total activities in range (before the drill-down cap)


# --- Responses (envelope-wrapped, like the rest of the API) ---


class CampaignsListResponse(BaseResponse):
    campaigns: list[CampaignOut]


class CampaignDetailResponse(BaseResponse):
    campaign: CampaignOut


class LeadCaptureResponse(BaseResponse):
    lead: LeadOut


class LeadsListResponse(BaseResponse):
    leads: list[LeadOut]
    total: int


class CampaignStatsResponse(BaseResponse):
    stats: CampaignStats


class CampaignActivitiesResponse(BaseResponse):
    activities: CampaignActivities
