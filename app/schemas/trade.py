from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, EmailStr, Field

from app.schemas.responses import BaseResponse

# --- Requests ---


class TradeRegistrationCreateRequest(BaseModel):
    """Public capture payload — same top-level shape as the old campaigns
    LeadCreateRequest (first_name/last_name/email/phone/company/job_title +
    a catch-all `responses` dict) so a form already posting to
    POST /campaigns/{slug}/leads only has to change its URL, not its body.
    Everything Trade-specific (company/sector/financials/international trade/
    consent/the optional second_participant object) lives in `responses`,
    matching the real payload shape already in production."""

    first_name: str = Field(min_length=1, max_length=120)
    last_name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    phone: str | None = Field(default=None, max_length=40)
    company: str | None = Field(default=None, max_length=200)
    job_title: str | None = Field(default=None, max_length=200)
    source: str = "form"
    captured_at: datetime | None = None
    responses: dict[str, Any] = Field(default_factory=dict)


# --- Read models ---


class TradeProgramOut(BaseModel):
    id: int
    slug: str
    name: str
    kind: str
    status: str
    starts_on: date | None = None
    ends_on: date | None = None
    timezone: str
    config: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class TradeProgramStats(BaseModel):
    total_registrations: int
    total_participants: int
    crm_sync_breakdown: dict[str, int]
    eligibility_breakdown: dict[str, int]


class TradeParticipantRef(BaseModel):
    """Light reference to a co-participant, embedded on the other participant's
    row so the UI can show the pair without a second request."""

    id: int
    first_name: str
    last_name: str
    email: str | None = None
    is_primary: bool


class TradeLeadOut(BaseModel):
    id: int
    trade_program_id: int
    registration_id: str
    participant_index: int
    is_primary: bool
    co_participant: TradeParticipantRef | None = None

    first_name: str
    middle_name: str | None = None
    last_name: str
    email: str
    phone: str | None = None
    job_title: str | None = None

    company: str | None = None
    registered_address: str | None = None
    city: str | None = None
    postal_code: str | None = None
    country: str | None = None
    company_founded: str | None = None
    industry_sector: str | None = None
    sector_specification: str | None = None
    sector_other: str | None = None
    ownership: list[str] | None = None
    operating_currency: str | None = None
    fiscal_year_start: str | None = None
    employee_count: str | None = None
    sources_internationally: str | None = None
    source_countries: list[str] | None = None
    sells_internationally: str | None = None
    sales_countries: list[str] | None = None
    topics_of_interest: list[str] | None = None
    consent_terms: bool | None = None
    consent_data_processing: bool | None = None
    consent_liability_waiver: bool | None = None
    consent_photo_video: bool | None = None
    cohort_date: str | None = None
    wtc_location: str | None = None

    source: str
    tags: list[str] | None = None
    captured_at: datetime | None = None
    created_at: datetime

    crm_sync_status: str
    crm_synced_at: datetime | None = None
    crm_contact_id: str | None = None
    crm_error: str | None = None

    opened_at: datetime | None = None
    opened_count: int = 0
    clicked_materials: list[str] | None = None

    eligibility_status: str
    eligibility_submitted_at: datetime | None = None


class TradeDocumentOut(BaseModel):
    id: int
    document_key: str
    file_name: str
    content_type: str | None = None
    size_bytes: int
    uploaded_at: datetime
    download_url: str | None = None


class TradeRegistrationOut(BaseModel):
    """A registration with both participant rows (0, 1 or 2 depending on
    whether a 2nd participant was ever added) plus the shared fields, for the
    detail dialog."""

    registration_id: str
    participants: list[TradeLeadOut]


# --- Responses (envelope-wrapped, like the rest of the API) ---


class TradeProgramsListResponse(BaseResponse):
    programs: list[TradeProgramOut]


class TradeProgramDetailResponse(BaseResponse):
    program: TradeProgramOut
    stats: TradeProgramStats


class TradeLeadsListResponse(BaseResponse):
    leads: list[TradeLeadOut]
    total: int


class TradeLeadDetailResponse(BaseResponse):
    lead: TradeLeadOut


class TradeRegistrationDetailResponse(BaseResponse):
    registration: TradeRegistrationOut


class TradeRegistrationCaptureResponse(BaseResponse):
    registration: TradeRegistrationOut
    created: bool


class TradeEligibilitySubmitResponse(BaseResponse):
    document: TradeDocumentOut
    eligibility_status: str


class TradeDocumentsListResponse(BaseResponse):
    documents: list[TradeDocumentOut]
