from datetime import datetime

from sqlalchemy import BigInteger, Column, DateTime, func
from sqlmodel import Field, SQLModel


class Owner(SQLModel, table=True):
    __tablename__ = "owners"

    id: int = Field(sa_column=Column(BigInteger, primary_key=True))
    display_name: str
    email: str | None = None
    is_active: bool = True
    updated_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    )
