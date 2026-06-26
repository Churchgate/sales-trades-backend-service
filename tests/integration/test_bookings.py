from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.endpoints import bookings as bookings_api
from app.core.config import Settings
from app.models.booking import STATUS_CANCELLED, STATUS_CONFIRMED, Booking
from app.models.room import Room
from app.repositories import bookings_repo, rooms_repo
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


async def test_booking_out_includes_created_at(db_session: AsyncSession) -> None:
    room = await _make_room(db_session)
    start, end = _slot(24)
    booking, _, _ = await booking_service.create_booking(
        db_session, room_id=room.id, booker_name="A", booker_email="a@example.com",
        start_time=start, end_time=end,
    )
    resp = await bookings_api.get_booking(booking.access_code, db_session)
    assert resp.booking.created_at is not None


async def test_room_detail_endpoint(db_session: AsyncSession) -> None:
    room = await _make_room(db_session)
    resp = await bookings_api.get_room(room.id, db_session)
    assert resp.room.id == room.id
    assert resp.room.name == room.name


async def test_room_detail_endpoint_404(db_session: AsyncSession) -> None:
    with pytest.raises(HTTPException) as exc_info:
        await bookings_api.get_room(999, db_session)
    assert exc_info.value.status_code == 404


async def test_bookings_by_email_returns_matches(db_session: AsyncSession) -> None:
    room = await _make_room(db_session)
    start, end = _slot(24)
    await booking_service.create_booking(
        db_session, room_id=room.id, booker_name="A", booker_email="match@example.com",
        start_time=start, end_time=end,
    )
    resp = await bookings_api.list_bookings_by_email("match@example.com", db_session)
    assert len(resp.bookings) == 1
    assert resp.bookings[0].booker_email == "match@example.com"


async def test_bookings_by_email_unknown_email_returns_empty(db_session: AsyncSession) -> None:
    resp = await bookings_api.list_bookings_by_email("nobody@example.com", db_session)
    assert resp.bookings == []


async def test_admin_bookings_lists_all_statuses_and_filters_by_room(
    db_session: AsyncSession,
) -> None:
    room_a = await _make_room(db_session, name="Room A")
    room_b = await _make_room(db_session, name="Room B")
    start, end = _slot(24)
    confirmed, _, _ = await booking_service.create_booking(
        db_session, room_id=room_a.id, booker_name="A", booker_email="a@example.com",
        start_time=start, end_time=end,
    )
    other_start, other_end = _slot(48)
    await booking_service.create_booking(
        db_session, room_id=room_b.id, booker_name="B", booker_email="b@example.com",
        start_time=other_start, end_time=other_end,
    )
    await booking_service.cancel_booking(db_session, confirmed.access_code)

    resp = await bookings_api.admin_list_bookings(db_session, room_id=None)
    assert len(resp.bookings) == 2
    assert {b.status for b in resp.bookings} == {STATUS_CANCELLED, STATUS_CONFIRMED}

    filtered = await bookings_api.admin_list_bookings(db_session, room_id=room_a.id)
    assert len(filtered.bookings) == 1
    assert filtered.bookings[0].room_id == room_a.id


async def test_admin_stats_counts(db_session: AsyncSession) -> None:
    room = await _make_room(db_session)
    now = datetime.now(UTC)

    later_today = now + timedelta(hours=2)
    await booking_service.create_booking(
        db_session, room_id=room.id, booker_name="A", booker_email="a@example.com",
        start_time=later_today, end_time=later_today + timedelta(hours=1),
    )

    # In progress right now — bypasses the service's "no past start" rule via a
    # direct repo insert, since a real in-progress booking was valid when made.
    active = Booking(
        room_id=room.id, booker_name="B", booker_email="b@example.com",
        start_time=now - timedelta(minutes=30), end_time=now + timedelta(minutes=30),
        access_code="111111", status=STATUS_CONFIRMED,
    )
    await bookings_repo.create_booking(db_session, active)

    cancelled, _, _ = await booking_service.create_booking(
        db_session, room_id=room.id, booker_name="C", booker_email="c@example.com",
        start_time=now + timedelta(hours=5), end_time=now + timedelta(hours=6),
    )
    await booking_service.cancel_booking(db_session, cancelled.access_code)

    resp = await bookings_api.admin_list_bookings(db_session, room_id=None)
    assert resp.stats.total_rooms == 1
    assert resp.stats.all_time_count == 3
    assert resp.stats.active_now_count == 1
    assert resp.stats.today_count >= 1


async def test_admin_cancel_by_id_idempotent(db_session: AsyncSession) -> None:
    room = await _make_room(db_session)
    start, end = _slot(24)
    booking, _, _ = await booking_service.create_booking(
        db_session, room_id=room.id, booker_name="A", booker_email="a@example.com",
        start_time=start, end_time=end,
    )
    resp = await bookings_api.admin_cancel_booking(booking.id, db_session)
    assert resp.booking.status == STATUS_CANCELLED

    resp2 = await bookings_api.admin_cancel_booking(booking.id, db_session)
    assert resp2.booking.status == STATUS_CANCELLED


async def test_admin_cancel_by_id_unknown_id_raises(db_session: AsyncSession) -> None:
    with pytest.raises(HTTPException) as exc_info:
        await bookings_api.admin_cancel_booking(999, db_session)
    assert exc_info.value.status_code == 404


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
