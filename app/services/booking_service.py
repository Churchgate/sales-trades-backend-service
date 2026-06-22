"""Booking orchestration: validate, prevent double-booking, persist, and email.

The HTTP layer stays thin — endpoints translate these domain errors into status
codes. Bookings are confirmed instantly; the confirmation email (room, date, time,
access code) is best-effort and never rolls back a saved booking.
"""

import secrets
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.booking import STATUS_CANCELLED, STATUS_CONFIRMED, Booking
from app.models.room import Room
from app.repositories import bookings_repo, rooms_repo
from app.services import mailer

logger = get_logger(__name__)

# Numeric-only access code (fixed length, leading zeros allowed).
_CODE_ALPHABET = "0123456789"
_CODE_LENGTH = 6
_CODE_MAX_ATTEMPTS = 5


class BookingError(Exception):
    """Base for booking domain errors."""


class RoomNotFoundError(BookingError):
    pass


class InvalidBookingTimeError(BookingError):
    pass


class BookingConflictError(BookingError):
    pass


class BookingNotFoundError(BookingError):
    pass


def _generate_access_code() -> str:
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_LENGTH))


def _ensure_aware(dt: datetime) -> datetime:
    """Treat naive datetimes as UTC so comparisons are unambiguous."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


async def create_booking(
    session: AsyncSession,
    *,
    room_id: int,
    booker_name: str,
    booker_email: str,
    start_time: datetime,
    end_time: datetime,
    title: str | None = None,
) -> tuple[Booking, Room, bool]:
    """Create a confirmed booking and send its confirmation email.

    Returns (booking, room, email_sent). Raises a BookingError subclass on
    validation failure or a slot conflict.
    """
    start_time = _ensure_aware(start_time)
    end_time = _ensure_aware(end_time)

    if end_time <= start_time:
        raise InvalidBookingTimeError("end_time must be after start_time")
    if start_time < datetime.now(UTC):
        raise InvalidBookingTimeError("cannot book a slot in the past")

    room = await rooms_repo.get_room(session, room_id)
    if room is None or not room.is_active:
        raise RoomNotFoundError(f"room {room_id} not found or inactive")

    conflict = await bookings_repo.find_overlapping(session, room_id, start_time, end_time)
    if conflict is not None:
        raise BookingConflictError("the room is already booked for an overlapping time")

    booking = Booking(
        room_id=room_id,
        booker_name=booker_name,
        booker_email=booker_email,
        title=title,
        start_time=start_time,
        end_time=end_time,
        access_code=_generate_access_code(),
        status=STATUS_CONFIRMED,
    )

    # Retry on the (rare) access-code collision against the unique index.
    for attempt in range(_CODE_MAX_ATTEMPTS):
        try:
            booking = await bookings_repo.create_booking(session, booking)
            break
        except Exception:
            await session.rollback()
            if attempt == _CODE_MAX_ATTEMPTS - 1:
                raise
            booking.access_code = _generate_access_code()

    email_sent = await _send_confirmation(booking, room)
    return booking, room, email_sent


async def cancel_booking(session: AsyncSession, access_code: str) -> Booking:
    booking = await bookings_repo.get_by_access_code(session, access_code)
    if booking is None:
        raise BookingNotFoundError(f"no booking for access code {access_code}")
    return await _cancel(session, booking)


async def cancel_booking_by_id(session: AsyncSession, booking_id: int) -> Booking:
    booking = await bookings_repo.get_by_id(session, booking_id)
    if booking is None:
        raise BookingNotFoundError(f"no booking with id {booking_id}")
    return await _cancel(session, booking)


async def _cancel(session: AsyncSession, booking: Booking) -> Booking:
    if booking.status != STATUS_CANCELLED:
        booking.status = STATUS_CANCELLED
        booking = await bookings_repo.update_booking(session, booking)
    return booking


async def _send_confirmation(booking: Booking, room: Room) -> bool:
    settings = get_settings()
    tz = ZoneInfo(settings.booking_tz)
    start_local = booking.start_time.astimezone(tz)
    end_local = booking.end_time.astimezone(tz)

    date_str = start_local.strftime("%A, %d %B %Y")
    time_str = f"{start_local.strftime('%I:%M %p')} – {end_local.strftime('%I:%M %p')}"
    subject = f"Booking confirmed: {room.name} on {date_str}"

    text = (
        f"Hi {booking.booker_name},\n\n"
        f"Your booking is confirmed.\n\n"
        f"Room:   {room.name}"
        + (f" ({room.location})" if room.location else "")
        + "\n"
        f"Date:   {date_str}\n"
        f"Time:   {time_str} ({settings.booking_tz})\n"
        + (f"Purpose:{booking.title}\n" if booking.title else "")
        + f"\nAccess code: {booking.access_code}\n\n"
        f"Present this access code on arrival. Keep this email for your records.\n"
    )

    location_suffix = f" ({room.location})" if room.location else ""
    purpose_row = (
        f'<tr><td style="padding:6px 0;color:#666">Purpose</td>'
        f'<td style="padding:6px 0">{booking.title}</td></tr>'
        if booking.title
        else ""
    )
    footer = "Present this access code on arrival. Keep this email for your records."

    html = f"""\
<div style="font-family:Arial,Helvetica,sans-serif;max-width:560px;margin:auto;color:#1a1a1a">
  <h2 style="margin-bottom:4px">Booking confirmed</h2>
  <p>Hi {booking.booker_name}, your booking is confirmed.</p>
  <table style="border-collapse:collapse;width:100%;margin:16px 0">
    <tr><td style="padding:6px 0;color:#666">Room</td>
        <td style="padding:6px 0;font-weight:600">{room.name}{location_suffix}</td></tr>
    <tr><td style="padding:6px 0;color:#666">Date</td>
        <td style="padding:6px 0;font-weight:600">{date_str}</td></tr>
    <tr><td style="padding:6px 0;color:#666">Time</td>
        <td style="padding:6px 0;font-weight:600">{time_str} ({settings.booking_tz})</td></tr>
    {purpose_row}
  </table>
  <div style="background:#f4f6f8;border-radius:8px;padding:16px;text-align:center;margin:16px 0">
    <div style="color:#666;font-size:13px;letter-spacing:.04em">ACCESS CODE</div>
    <div style="font-size:28px;font-weight:700;letter-spacing:.18em;margin-top:4px">\
{booking.access_code}</div>
  </div>
  <p style="color:#666;font-size:13px">{footer}</p>
</div>
"""

    return await mailer.send_email(
        to_email=booking.booker_email, subject=subject, html=html, text=text, settings=settings
    )
