from datetime import date

from sqlalchemy import BigInteger, Column, ForeignKey, Numeric
from sqlmodel import Field, SQLModel


class PipelineDailySnapshot(SQLModel, table=True):
    __tablename__ = "pipeline_daily_snapshot"

    snapshot_date: date = Field(primary_key=True)
    pipeline_id: int = Field(
        sa_column=Column(BigInteger, ForeignKey("pipelines.id"), primary_key=True)
    )
    stage_id: int = Field(
        sa_column=Column(BigInteger, ForeignKey("stages.id"), primary_key=True)
    )
    deal_count: int | None = None
    total_value: float | None = Field(default=None, sa_column=Column(Numeric))
    total_base_currency_value: float | None = Field(default=None, sa_column=Column(Numeric))
