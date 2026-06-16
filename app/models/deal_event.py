from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Column, DateTime, Index
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class DealEvent(SQLModel, table=True):
    __tablename__ = "deal_events"
    __table_args__ = (Index("idx_deal_events_deal", "deal_id", "occurred_at"),)

    id: int | None = Field(
        default=None, sa_column=Column(BigInteger, primary_key=True, autoincrement=True)
    )
    deal_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    event_type: str  # 'created' | 'stage_change' | 'owner_change'

    old_pipeline_id: int | None = Field(default=None, sa_column=Column(BigInteger))
    old_stage_id: int | None = Field(default=None, sa_column=Column(BigInteger))
    new_pipeline_id: int | None = Field(default=None, sa_column=Column(BigInteger))
    new_stage_id: int | None = Field(default=None, sa_column=Column(BigInteger))
    old_owner_id: int | None = Field(default=None, sa_column=Column(BigInteger))
    new_owner_id: int | None = Field(default=None, sa_column=Column(BigInteger))

    occurred_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    source: str  # 'webhook' | 'timeline_backfill'
    raw_payload: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB))
