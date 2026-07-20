from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, Index, Integer, String, func, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlmodel import Field, SQLModel

# Triage state on the Hot Leads queue — independent of CRM sync/pack delivery.
TRIAGE_NEW = "new"
TRIAGE_CONTACTED = "contacted"
TRIAGE_DISMISSED = "dismissed"
TRIAGE_SNOOZED = "snoozed"

# CRM sync lifecycle for a captured lead. Capture never depends on the CRM being
# reachable: a lead is saved 'pending' and a background job pushes it later.
CRM_PENDING = "pending"
CRM_SYNCED = "synced"
CRM_FAILED = "failed"
CRM_SKIPPED = "skipped"  # live sync disabled, or no-sync campaign

# Digital-pack delivery lifecycle. The "Send Me the Digital Pack" form promises
# the visitor their selected materials by email — capture saves the request and a
# background job (mirroring CRM sync) emails the assets, so capture never blocks
# on email being reachable. Same offline-safe shape as the CRM lifecycle above.
PACK_NOT_REQUESTED = "not_requested"  # no deliverable materials on this lead
PACK_PENDING = "pending"
PACK_SENT = "sent"
PACK_FAILED = "failed"
PACK_SKIPPED = "skipped"  # email transport not configured


class Lead(SQLModel, table=True):
    """A visitor captured at a stand/booth, scoped to a campaign.

    Well-known columns back the dashboard stats, CSV export and CRM sync; the
    raw submission is also kept verbatim in `responses` (JSONB) so per-event
    fields are never lost and a future AI layer has clean, complete data.
    """

    __tablename__ = "leads"
    __table_args__ = (
        # Stats/listing scan by campaign over time; CRM job scans by sync status;
        # one lead per email per campaign (dedup — email is normalised lowercase).
        Index("idx_leads_campaign_created", "campaign_id", "created_at"),
        Index("idx_leads_campaign_sync", "campaign_id", "crm_sync_status"),
        Index("idx_leads_campaign_email", "campaign_id", "email", unique=True),
        Index("idx_leads_interests", "interests", postgresql_using="gin"),
        # Delivery job scans pending/failed packs across campaigns (like CRM sync).
        Index("idx_leads_campaign_pack", "campaign_id", "pack_delivery_status"),
        # Cross-campaign Hot Leads queue sorts/filters by these directly in SQL.
        Index("idx_leads_engagement_score", "engagement_score"),
        Index("idx_leads_triage_status", "triage_status"),
    )

    id: int | None = Field(
        default=None, sa_column=Column(BigInteger, primary_key=True, autoincrement=True)
    )
    campaign_id: int = Field(
        sa_column=Column(BigInteger, ForeignKey("campaigns.id"), nullable=False)
    )

    first_name: str
    last_name: str
    email: str
    phone: str
    company: str
    job_title: str | None = None

    source: str = Field(default="qr")  # qr | tablet | badge_scan | ...
    device_id: str | None = None
    timing: str | None = None  # immediate | 0-3m | 3-6m | 6-12m | future

    interests: list[str] | None = Field(default=None, sa_column=Column(ARRAY(String)))
    requested_materials: list[str] | None = Field(
        default=None, sa_column=Column(ARRAY(String))
    )
    tags: list[str] | None = Field(default=None, sa_column=Column(ARRAY(String)))

    inspection_requested: bool = Field(default=False)
    inspection_type: str | None = None
    marketing_opt_in: bool = Field(default=False)
    consent_status: bool = Field(default=False)
    consent_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )

    # Client-supplied capture time (offline-safe); created_at is the server's record.
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

    # Digital-pack email delivery. `pack_delivered_materials` records exactly which
    # materials were emailed (resolved against the campaign's asset map) — kept
    # alongside the verbatim `requested_materials` so the request-vs-fulfilment gap
    # is analysable per lead.
    pack_delivery_status: str = Field(default=PACK_NOT_REQUESTED)
    pack_delivered_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    pack_delivered_materials: list[str] | None = Field(
        default=None, sa_column=Column(ARRAY(String))
    )
    pack_delivery_error: str | None = None

    # Engagement, from SendGrid Event Webhook (services/email_event_ingest.py). A
    # rollup for dashboard convenience; the full per-event history (incl. which
    # document each click was for) lives in the email_events table.
    pack_opened_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    pack_opened_count: int = Field(default=0)
    pack_clicked_materials: list[str] | None = Field(
        default=None, sa_column=Column(ARRAY(String))
    )

    # Persisted engagement score (services/lead_scoring.py) — was previously
    # computed per-request in Python only, which meant it could never be
    # SQL-sorted; the cross-campaign Hot Leads queue needs ORDER BY in the DB.
    engagement_score: int = Field(default=0, sa_column=Column(Integer, nullable=False))
    score_computed_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )

    # ICP company-fit score (services/icp_scoring.py, OpenRouter-based). Scored
    # once per lead (batch/backfill), not recomputed per request like
    # engagement_score, since it depends on an LLM call.
    icp_score: int | None = None
    icp_tier: str | None = None
    icp_rationale: str | None = None

    # Sales triage state on the Hot Leads queue — independent of crm_sync_status
    # and pack_delivery_status, which track system delivery, not human follow-up.
    triage_status: str = Field(default=TRIAGE_NEW)
    triage_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    triage_by: str | None = None
