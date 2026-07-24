from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlmodel import Field, SQLModel

# CRM sync lifecycle — same offline-safe contract as app.models.lead: a row is
# saved 'pending' and a background job pushes it later, never blocking capture.
CRM_PENDING = "pending"
CRM_SYNCED = "synced"
CRM_FAILED = "failed"
CRM_SKIPPED = "skipped"

# Eligibility-document lifecycle. Columns only for now — the submission flow
# (via wtcabuja.com, not a dashboard-minted link) lands in a later phase.
ELIGIBILITY_NOT_REQUESTED = "not_requested"
ELIGIBILITY_PENDING = "pending"
ELIGIBILITY_SUBMITTED = "submitted"
ELIGIBILITY_APPROVED = "approved"
ELIGIBILITY_REJECTED = "rejected"


class TradeLead(SQLModel, table=True):
    """One participant of a Trade program registration.

    A registration (the wtcabuja.com/export-launchpad/apply form) may name up
    to two participants — a required primary and an OPTIONAL second ("each
    company sends two representatives", but not compulsory). Rather than a
    parent/child table, the two are two flat rows sharing `registration_id`
    (`participant_index`/`is_primary` order them) — each independently
    listable, filterable and CRM-syncable, at the cost of duplicating the
    shared registration-level fields (company, sector, financials, ...)
    across both rows. The full verbatim submission is also kept in
    `responses` (JSONB) so nothing from the form is ever lost, matching the
    same escape-hatch rationale as Lead.responses.
    """

    __tablename__ = "trade_leads"
    __table_args__ = (
        # One row per email per program — but the 2nd participant's email is
        # optional, so this must NOT reject multiple empty-email rows.
        Index(
            "idx_trade_leads_program_email",
            "trade_program_id",
            "email",
            unique=True,
            postgresql_where=text("email <> ''"),
        ),
        Index("idx_trade_leads_registration", "registration_id", "participant_index"),
        Index("idx_trade_leads_program_created", "trade_program_id", "created_at"),
        Index("idx_trade_leads_crm_sync", "crm_sync_status"),
    )

    id: int | None = Field(
        default=None, sa_column=Column(BigInteger, primary_key=True, autoincrement=True)
    )
    trade_program_id: int = Field(
        sa_column=Column(BigInteger, ForeignKey("trade_programs.id"), nullable=False)
    )

    # Registration linkage — same value on both participant rows of one
    # registration; lets the API/UI pair them without a parent table.
    registration_id: str = Field(sa_column=Column(String, nullable=False))
    participant_index: int = Field(default=1, sa_column=Column(Integer, nullable=False))
    is_primary: bool = Field(default=True, sa_column=Column(Boolean, nullable=False))

    # Per-participant identity (differs between the two rows of a registration).
    first_name: str
    middle_name: str | None = None
    last_name: str
    email: str = Field(default="")
    phone: str | None = None
    job_title: str | None = None

    # Shared registration fields (identical on both rows — company/sector/
    # financials/trade profile). Field names match the real production
    # `responses` payload verified on the existing export-launchpad-2026
    # campaign leads, not just the public form copy.
    company: str | None = None
    registered_address: str | None = None
    city: str | None = None
    postal_code: str | None = None
    country: str | None = None
    company_founded: str | None = None
    industry_sector: str | None = None
    sector_specification: str | None = None
    sector_other: str | None = None
    ownership: list[str] | None = Field(default=None, sa_column=Column(ARRAY(String)))
    operating_currency: str | None = None
    fiscal_year_start: str | None = None
    employee_count: str | None = None
    sources_internationally: str | None = None
    source_countries: list[str] | None = Field(default=None, sa_column=Column(ARRAY(String)))
    sells_internationally: str | None = None
    sales_countries: list[str] | None = Field(default=None, sa_column=Column(ARRAY(String)))
    topics_of_interest: list[str] | None = Field(default=None, sa_column=Column(ARRAY(String)))
    consent_terms: bool | None = None
    consent_data_processing: bool | None = None
    consent_liability_waiver: bool | None = None
    consent_photo_video: bool | None = None
    cohort_date: str | None = None
    wtc_location: str | None = None

    source: str = Field(default="form")  # form | campaign_transfer | sheet_import
    tags: list[str] | None = Field(default=None, sa_column=Column(ARRAY(String)))
    captured_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), server_default=func.now())
    )

    responses: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )

    crm_sync_status: str = Field(default=CRM_PENDING)
    crm_synced_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    crm_contact_id: str | None = None
    crm_error: str | None = None

    # Email engagement rollups — the Trade equivalents of Lead's
    # pack_opened_at/pack_opened_count/pack_clicked_materials, so tracking
    # transferred from an existing campaign lead isn't lost.
    opened_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    opened_count: int = Field(default=0, sa_column=Column(Integer, nullable=False))
    clicked_materials: list[str] | None = Field(default=None, sa_column=Column(ARRAY(String)))

    # Eligibility lifecycle placeholder — see module docstring.
    eligibility_status: str = Field(default=ELIGIBILITY_NOT_REQUESTED)
    eligibility_submitted_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )

    # Rep-scoping placeholder, unused until a later phase wires OwnerScopeDep
    # into the /trade endpoints.
    owner_id: int | None = Field(
        default=None, sa_column=Column(BigInteger, ForeignKey("owners.id"), nullable=True)
    )
