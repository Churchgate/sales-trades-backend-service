from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Column, DateTime, Index
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class EmailActivity(SQLModel, table=True):
    __tablename__ = "email_activity"
    __table_args__ = (Index("idx_email_activity_deal", "deal_id", "conversation_time"),)

    id: int | None = Field(
        default=None, sa_column=Column(BigInteger, primary_key=True, autoincrement=True)
    )
    deal_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    direction: str  # 'incoming' | 'outgoing'
    conversation_time: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    subject: str | None = None
    raw_payload: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB))
