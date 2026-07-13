from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Column, DateTime, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

# Activity kinds we roll up per salesperson on NOG contacts. Meetings are logged as
# Freshsales sales activities alongside calls; notes/emails come from their own endpoints.
ACTIVITY_CALL = "call"
ACTIVITY_EMAIL = "email"
ACTIVITY_MEETING = "meeting"
ACTIVITY_NOTE = "note"


class ContactActivity(SQLModel, table=True):
    """One outreach activity (call / email / meeting / note) logged on a campaign
    contact in Freshsales, attributed to the contact's assigned owner.

    NOG leads are Freshsales contacts with no deals, so the deal-centric activity sync
    (EmailActivity/TaskSnapshot) never sees them. This table is populated by
    `nog_activity_sync`, which fans out per contact (Freshsales has no bulk activity
    API). `owner_id`/`owner_name`/`prospect_tier` are snapshotted from the contact so the
    dashboard can group/filter by salesperson and by Ops' Strategic/Standard tier without
    a live CRM call. `source_key` is the Freshsales-derived dedup key (e.g. "email:123")
    so re-syncs are idempotent.
    """

    __tablename__ = "contact_activity"
    __table_args__ = (
        Index("idx_contact_activity_owner", "campaign_id", "owner_id", "occurred_at"),
        Index("idx_contact_activity_tier", "campaign_id", "prospect_tier", "occurred_at"),
        Index("idx_contact_activity_source", "source_key", unique=True),
    )

    id: int | None = Field(
        default=None, sa_column=Column(BigInteger, primary_key=True, autoincrement=True)
    )
    campaign_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    contact_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    contact_name: str | None = None
    owner_id: int | None = Field(default=None, sa_column=Column(BigInteger))
    owner_name: str | None = None  # denormalised so the summary needs no owner join
    prospect_tier: str | None = None  # "Strategic" | "Standard" (Freshsales cf_prospect_tier)
    activity_type: str = Field(sa_column=Column(String, nullable=False))  # call|email|meeting|note
    direction: str | None = None  # 'incoming' | 'outgoing' (emails)
    occurred_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    subject: str | None = None
    source_key: str = Field(sa_column=Column(String, nullable=False))
    raw: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB))
    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), server_default=func.now())
    )
