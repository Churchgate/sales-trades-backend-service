from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, EmailStr, Field

from app.schemas.responses import BaseResponse

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


class LeadCreateRequest(BaseModel):
    first_name: str = Field(min_length=1, max_length=120)
    last_name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    phone: str = Field(min_length=1, max_length=40)
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


class DayCount(BaseModel):
    day: date
    count: int


class CampaignStats(BaseModel):
    total_leads: int
    inspection_requests: int
    marketing_opt_ins: int
    synced_count: int
    unsynced_count: int
    by_interest: dict[str, int]
    by_source: dict[str, int]
    by_day: list[DayCount]


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
