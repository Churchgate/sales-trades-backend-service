from datetime import date, datetime
from typing import Any

from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, Index, Numeric, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class DealSnapshot(SQLModel, table=True):
    __tablename__ = "deals_snapshot"
    __table_args__ = (
        Index("idx_deals_snapshot_pipeline_stage", "pipeline_id", "stage_id"),
        Index("idx_deals_snapshot_owner", "owner_id"),
    )

    deal_id: int = Field(sa_column=Column(BigInteger, primary_key=True))
    pipeline_id: int | None = Field(
        default=None, sa_column=Column(BigInteger, ForeignKey("pipelines.id"))
    )
    stage_id: int | None = Field(
        default=None, sa_column=Column(BigInteger, ForeignKey("stages.id"))
    )
    owner_id: int | None = Field(
        default=None, sa_column=Column(BigInteger, ForeignKey("owners.id"))
    )
    name: str | None = None
    amount: float | None = Field(default=None, sa_column=Column(Numeric))
    base_currency_amount: float | None = Field(default=None, sa_column=Column(Numeric))
    expected_close_date: date | None = None
    stage_updated_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True))
    )
    age_days: int | None = None
    rotten_days: int | None = None
    sales_account_id: int | None = Field(default=None, sa_column=Column(BigInteger))
    sales_account_name: str | None = None

    # --- curated custom fields (spec §3) ---
    cf_project: str | None = None
    cf_floor: str | None = None
    cf_sqm_size: float | None = Field(default=None, sa_column=Column(Numeric))
    cf_product_category: str | None = None
    cf_term: float | None = Field(default=None, sa_column=Column(Numeric))
    cf_start_date: date | None = None
    cf_term_end_date: date | None = None
    cf_deal_status: str | None = None
    cf_total_lease_amount: float | None = Field(default=None, sa_column=Column(Numeric))

    custom_fields: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB))
    raw_payload: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB))

    updated_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    )
    last_synced_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    )
