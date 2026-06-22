from datetime import datetime

from sqlalchemy import and_, func, select
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


async def get_by_id(session: AsyncSession, booking_id: int) -> Booking | None:
    return await session.get(Booking, booking_id)


async def list_by_email(session: AsyncSession, email: str) -> list[Booking]:
    """All bookings (any status) for an email, most recent start first."""
    result = await session.execute(
        select(Booking)
        .where(Booking.booker_email == email)
        .order_by(Booking.start_time.desc())
    )
    return list(result.scalars().all())


async def list_all(session: AsyncSession, *, room_id: int | None = None) -> list[Booking]:
    """All bookings (any status), for the admin dashboard. Optionally filtered by room."""
    stmt = select(Booking)
    if room_id is not None:
        stmt = stmt.where(Booking.room_id == room_id)
    result = await session.execute(stmt.order_by(Booking.start_time.desc()))
    return list(result.scalars().all())


async def count_today(session: AsyncSession, day_start: datetime, day_end: datetime) -> int:
    """Confirmed bookings overlapping [day_start, day_end)."""
    result = await session.execute(
        select(func.count()).select_from(Booking).where(
            and_(
                Booking.status == STATUS_CONFIRMED,
                Booking.start_time < day_end,
                Booking.end_time > day_start,
            )
        )
    )
    return result.scalar_one()


async def count_active_now(session: AsyncSession, now: datetime) -> int:
    """Confirmed bookings currently in progress."""
    result = await session.execute(
        select(func.count()).select_from(Booking).where(
            and_(
                Booking.status == STATUS_CONFIRMED,
                Booking.start_time <= now,
                Booking.end_time > now,
            )
        )
    )
    return result.scalar_one()


async def count_all_time(session: AsyncSession) -> int:
    """Every booking ever made, any status."""
    result = await session.execute(select(func.count()).select_from(Booking))
    return result.scalar_one()


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
