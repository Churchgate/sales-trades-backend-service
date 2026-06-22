from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.endpoints import bookings as bookings_api
from app.core.config import Settings
from app.models.booking import STATUS_CANCELLED, STATUS_CONFIRMED
from app.models.room import Room
from app.repositories import rooms_repo
from app.schemas.bookings import BookingCreateRequest
from app.services import booking_service, mailer


async def _make_room(session: AsyncSession, name: str = "Boardroom A") -> Room:
    return await rooms_repo.create_room(
        session, Room(name=name, location="3rd Floor", capacity=12, is_active=True)
    )


def _slot(hours_from_now: int, duration_hours: int = 1) -> tuple[datetime, datetime]:
    start = datetime.now(UTC) + timedelta(hours=hours_from_now)
    return start, start + timedelta(hours=duration_hours)


# --- service layer ---


async def test_create_booking_confirms_and_generates_code(db_session: AsyncSession) -> None:
    room = await _make_room(db_session)
    start, end = _slot(24)

    booking, returned_room, email_sent = await booking_service.create_booking(
        db_session,
        room_id=room.id,
        booker_name="Ada Lovelace",
        booker_email="ada@example.com",
        start_time=start,
        end_time=end,
        title="Strategy sync",
    )

    assert booking.status == STATUS_CONFIRMED
    assert len(booking.access_code) == 6
    assert booking.access_code.isdigit()
    assert returned_room.id == room.id
    # No SendGrid key configured in tests -> send is skipped, not an error.
    assert email_sent is False


async def test_overlapping_booking_rejected(db_session: AsyncSession) -> None:
    room = await _make_room(db_session)
    start, end = _slot(24)
    await booking_service.create_booking(
        db_session, room_id=room.id, booker_name="A", booker_email="a@example.com",
        start_time=start, end_time=end,
    )

    # Overlaps the back half of the first booking.
    with pytest.raises(booking_service.BookingConflictError):
        await booking_service.create_booking(
            db_session, room_id=room.id, booker_name="B", booker_email="b@example.com",
            start_time=start + timedelta(minutes=30), end_time=end + timedelta(minutes=30),
        )


async def test_adjacent_booking_allowed(db_session: AsyncSession) -> None:
    room = await _make_room(db_session)
    start, end = _slot(24)
    await booking_service.create_booking(
        db_session, room_id=room.id, booker_name="A", booker_email="a@example.com",
        start_time=start, end_time=end,
    )
    # Starts exactly when the first ends — half-open intervals don't overlap.
    booking, _, _ = await booking_service.create_booking(
        db_session, room_id=room.id, booker_name="B", booker_email="b@example.com",
        start_time=end, end_time=end + timedelta(hours=1),
    )
    assert booking.status == STATUS_CONFIRMED


async def test_end_before_start_rejected(db_session: AsyncSession) -> None:
    room = await _make_room(db_session)
    start, end = _slot(24)
    with pytest.raises(booking_service.InvalidBookingTimeError):
        await booking_service.create_booking(
            db_session, room_id=room.id, booker_name="A", booker_email="a@example.com",
            start_time=end, end_time=start,
        )


async def test_past_booking_rejected(db_session: AsyncSession) -> None:
    room = await _make_room(db_session)
    start, end = _slot(-5)
    with pytest.raises(booking_service.InvalidBookingTimeError):
        await booking_service.create_booking(
            db_session, room_id=room.id, booker_name="A", booker_email="a@example.com",
            start_time=start, end_time=end,
        )


async def test_unknown_room_rejected(db_session: AsyncSession) -> None:
    start, end = _slot(24)
    with pytest.raises(booking_service.RoomNotFoundError):
        await booking_service.create_booking(
            db_session, room_id=999, booker_name="A", booker_email="a@example.com",
            start_time=start, end_time=end,
        )


async def test_cancel_frees_the_slot(db_session: AsyncSession) -> None:
    room = await _make_room(db_session)
    start, end = _slot(24)
    booking, _, _ = await booking_service.create_booking(
        db_session, room_id=room.id, booker_name="A", booker_email="a@example.com",
        start_time=start, end_time=end,
    )

    cancelled = await booking_service.cancel_booking(db_session, booking.access_code)
    assert cancelled.status == STATUS_CANCELLED

    # Same slot can now be booked again.
    rebooked, _, _ = await booking_service.create_booking(
        db_session, room_id=room.id, booker_name="B", booker_email="b@example.com",
        start_time=start, end_time=end,
    )
    assert rebooked.status == STATUS_CONFIRMED


# --- API layer ---


async def test_create_booking_endpoint_returns_envelope(db_session: AsyncSession) -> None:
    room = await _make_room(db_session)
    start, end = _slot(48)
    resp = await bookings_api.create_booking(
        BookingCreateRequest(
            room_id=room.id, booker_name="Grace", booker_email="grace@example.com",
            start_time=start, end_time=end,
        ),
        db_session,
    )
    assert resp.status_code == 201
    assert resp.booking.access_code
    assert resp.booking.room_name == room.name


async def test_room_bookings_endpoint_lists_slots(db_session: AsyncSession) -> None:
    room = await _make_room(db_session)
    start, end = _slot(24)
    await booking_service.create_booking(
        db_session, room_id=room.id, booker_name="A", booker_email="a@example.com",
        start_time=start, end_time=end,
    )
    resp = await bookings_api.room_bookings(room.id, db_session, day=start.date())
    assert len(resp.slots) == 1


async def test_get_booking_by_code(db_session: AsyncSession) -> None:
    room = await _make_room(db_session)
    start, end = _slot(24)
    booking, _, _ = await booking_service.create_booking(
        db_session, room_id=room.id, booker_name="A", booker_email="a@example.com",
        start_time=start, end_time=end,
    )
    # Surrounding whitespace is tolerated (codes are 6-digit numeric).
    resp = await bookings_api.get_booking(f" {booking.access_code} ", db_session)
    assert resp.booking.access_code == booking.access_code


# --- mailer ---


async def test_mailer_sends_when_configured() -> None:
    settings = Settings(sendgrid_api_key="SG.test", mail_from_email="from@example.com")
    with respx.mock(base_url="https://api.sendgrid.com") as router:
        route = router.post("/v3/mail/send").mock(return_value=httpx.Response(202))
        ok = await mailer.send_email(
            "to@example.com", "Subject", "<p>hi</p>", "hi", settings=settings
        )
    assert ok is True
    assert route.called


async def test_mailer_skips_without_key() -> None:
    settings = Settings(sendgrid_api_key="")
    ok = await mailer.send_email("to@example.com", "S", "<p>h</p>", "h", settings=settings)
    assert ok is False


async def test_create_booking_marks_email_sent(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With a SendGrid key configured, a successful send sets email_sent=True."""
    room = await _make_room(db_session)
    start, end = _slot(24)

    # _send_confirmation reads settings via booking_service.get_settings.
    configured = Settings(sendgrid_api_key="SG.test", mail_from_email="from@example.com")
    monkeypatch.setattr(booking_service, "get_settings", lambda: configured)

    with respx.mock(base_url="https://api.sendgrid.com") as router:
        router.post("/v3/mail/send").mock(return_value=httpx.Response(202))
        _, _, email_sent = await booking_service.create_booking(
            db_session, room_id=room.id, booker_name="A", booker_email="a@example.com",
            start_time=start, end_time=end,
        )
    assert email_sent is True
