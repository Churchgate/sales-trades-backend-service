from datetime import date, datetime
from typing import Any

from sqlalchemy import BigInteger, Column, DateTime, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

# Trade program lifecycle — mirrors Campaign's (draft/active/archived).
STATUS_DRAFT = "draft"
STATUS_ACTIVE = "active"
STATUS_ARCHIVED = "archived"

# `kind` distinguishes the different Trade sub-areas sharing this table, e.g.
# Trade Programs (a boot camp cohort) vs. the future Trade Membership offering.
KIND_BOOT_CAMP = "boot_camp"
KIND_MEMBERSHIP = "membership"


class TradeProgram(SQLModel, table=True):
    """A Trade program/cohort (e.g. Export Launchpad Boot Camp 2026).

    Deliberately a separate table from `campaigns`: Trade contacts have a
    different direction (export readiness, eligibility screening) than the
    workspace/residential leads captured through campaigns, even though the
    shape (config-driven, one row per event) is intentionally similar.
    """

    __tablename__ = "trade_programs"

    id: int | None = Field(
        default=None, sa_column=Column(BigInteger, primary_key=True, autoincrement=True)
    )
    slug: str = Field(index=True, unique=True)  # e.g. 'export-launchpad-2026'
    name: str
    kind: str = Field(default=KIND_BOOT_CAMP)
    status: str = Field(default=STATUS_DRAFT)
    starts_on: date | None = None
    ends_on: date | None = None
    timezone: str = Field(default="Africa/Lagos")
    # Eligibility-criteria definition, required-document list, email template
    # reference — populated fully once the eligibility-submission phase lands.
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
