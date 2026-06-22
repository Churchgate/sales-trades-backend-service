from datetime import datetime

from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, Index, func
from sqlmodel import Field, SQLModel

# Booking lifecycle. Bookings are confirmed instantly (no approval step); a
# cancelled booking frees its slot so the room can be re-booked.
STATUS_CONFIRMED = "confirmed"
STATUS_CANCELLED = "cancelled"


class Booking(SQLModel, table=True):
    __tablename__ = "bookings"
    __table_args__ = (
        # Overlap checks query by room over a time window; cancel/lookup goes by code.
        Index("idx_bookings_room_time", "room_id", "start_time", "end_time"),
        Index("idx_bookings_access_code", "access_code", unique=True),
    )

    id: int | None = Field(
        default=None, sa_column=Column(BigInteger, primary_key=True, autoincrement=True)
    )
    room_id: int = Field(
        sa_column=Column(BigInteger, ForeignKey("rooms.id"), nullable=False)
    )
    booker_name: str
    booker_email: str
    title: str | None = None  # purpose / meeting subject

    start_time: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    end_time: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))

    access_code: str = Field(nullable=False)
    status: str = Field(default=STATUS_CONFIRMED)

    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), server_default=func.now())
    )
