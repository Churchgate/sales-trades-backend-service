from datetime import date, datetime
from typing import Any

from sqlalchemy import BigInteger, Column, DateTime, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

# Campaign lifecycle. A campaign is the reusable container for one event/activation;
# only an `active` campaign accepts public lead capture.
STATUS_DRAFT = "draft"
STATUS_ACTIVE = "active"
STATUS_ARCHIVED = "archived"


class Campaign(SQLModel, table=True):
    """A reusable lead-capture campaign (one per event/activation).

    The per-event form fields, interest/material options, CRM tag map, consent
    copy and branding all live in `config` (JSONB) so a new event is just a new
    row — no code or schema change. Seeded via scripts/seed_campaigns.py.
    """

    __tablename__ = "campaigns"

    id: int | None = Field(
        default=None, sa_column=Column(BigInteger, primary_key=True, autoincrement=True)
    )
    slug: str = Field(index=True, unique=True)  # e.g. 'nog-2026'
    name: str
    status: str = Field(default=STATUS_DRAFT)  # draft | active | archived
    starts_on: date | None = None
    ends_on: date | None = None
    timezone: str = Field(default="Africa/Lagos")
    config: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )

    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), server_default=func.now())
    )
    updated_at: datetime = Field(
        sa_column=Column(
            DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
        )
    )
