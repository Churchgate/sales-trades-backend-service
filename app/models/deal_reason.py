from datetime import datetime

from sqlalchemy import BigInteger, Column, DateTime, func
from sqlmodel import Field, SQLModel


class DealReason(SQLModel, table=True):
    """Lost/won deal-reason lookup (spec §B2 loss-reasons). Synced from the Freshsales
    `/selector/deal_reasons` selector; `deals_snapshot.lost_reason_id` references it."""

    __tablename__ = "deal_reasons"

    id: int = Field(sa_column=Column(BigInteger, primary_key=True))
    name: str
    updated_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    )
