from datetime import datetime

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.booking import STATUS_CONFIRMED, Booking


async def create_booking(session: AsyncSession, booking: Booking) -> Booking:
    session.add(booking)
    await session.commit()
    await session.refresh(booking)
    return booking


async def get_by_access_code(session: AsyncSession, access_code: str) -> Booking | None:
    result = await session.execute(
        select(Booking).where(Booking.access_code == access_code)
    )
    return result.scalars().first()


async def find_overlapping(
    session: AsyncSession, room_id: int, start_time: datetime, end_time: datetime
) -> Booking | None:
    """Return a confirmed booking on `room_id` that overlaps [start_time, end_time).

    Two half-open intervals overlap iff existing.start < new.end AND existing.end > new.start.
    """
    result = await session.execute(
        select(Booking).where(
            and_(
                Booking.room_id == room_id,
                Booking.status == STATUS_CONFIRMED,
                Booking.start_time < end_time,
                Booking.end_time > start_time,
            )
        )
    )
    return result.scalars().first()


async def list_for_room_between(
    session: AsyncSession, room_id: int, start: datetime, end: datetime
) -> list[Booking]:
    """Confirmed bookings on a room that intersect the [start, end) window (for availability)."""
    result = await session.execute(
        select(Booking)
        .where(
            and_(
                Booking.room_id == room_id,
                Booking.status == STATUS_CONFIRMED,
                Booking.start_time < end,
                Booking.end_time > start,
            )
        )
        .order_by(Booking.start_time)
    )
    return list(result.scalars().all())


async def update_booking(session: AsyncSession, booking: Booking) -> Booking:
    session.add(booking)
    await session.commit()
    await session.refresh(booking)
    return booking
