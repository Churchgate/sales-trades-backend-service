from datetime import datetime

from sqlalchemy import BigInteger, Column, DateTime, Index, func
from sqlmodel import Field, SQLModel


class TaskSnapshot(SQLModel, table=True):
    __tablename__ = "tasks_snapshot"
    __table_args__ = (Index("idx_tasks_owner_status", "owner_id", "status", "due_date"),)

    task_id: int = Field(sa_column=Column(BigInteger, primary_key=True))
    deal_id: int | None = Field(default=None, sa_column=Column(BigInteger))
    owner_id: int | None = Field(default=None, sa_column=Column(BigInteger))
    title: str | None = None
    status: str | None = None  # 'open' | 'completed'
    due_date: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    completed_date: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True))
    )
    last_synced_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    )
