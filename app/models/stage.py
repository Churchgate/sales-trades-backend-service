from datetime import datetime

from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, Numeric, func
from sqlmodel import Field, SQLModel


class Stage(SQLModel, table=True):
    __tablename__ = "stages"

    id: int = Field(sa_column=Column(BigInteger, primary_key=True))
    pipeline_id: int = Field(
        sa_column=Column(BigInteger, ForeignKey("pipelines.id"), nullable=False)
    )
    name: str
    position: int
    forecast_type: str
    probability: float | None = Field(default=None, sa_column=Column(Numeric))
    updated_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    )
