from datetime import datetime

from sqlalchemy import BigInteger, Column, DateTime, func
from sqlmodel import Field, SQLModel


class Pipeline(SQLModel, table=True):
    __tablename__ = "pipelines"

    id: int = Field(sa_column=Column(BigInteger, primary_key=True))
    name: str
    business_line: str
    is_default: bool = False
    is_active: bool = True
    # `/api/deals/view/{view_id}` id for the scheduled deal sync (spec §6B). Not
    # returned by any reference endpoint - configured manually and preserved
    # across reference syncs (never overwritten by upsert_pipeline).
    view_id: int | None = Field(default=None, sa_column=Column(BigInteger))
    updated_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    )
