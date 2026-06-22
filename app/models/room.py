from datetime import datetime

from sqlalchemy import BigInteger, Column, DateTime, func
from sqlmodel import Field, SQLModel


class Room(SQLModel, table=True):
    """A bookable hall or boardroom. Seeded via scripts/seed_rooms.py (no admin UI)."""

    __tablename__ = "rooms"

    id: int | None = Field(
        default=None, sa_column=Column(BigInteger, primary_key=True, autoincrement=True)
    )
    name: str = Field(index=True, unique=True)
    location: str | None = None
    capacity: int | None = None
    description: str | None = None
    is_active: bool = Field(default=True)

    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), server_default=func.now())
    )
