"""Public hall/boardroom booking API (no authentication, per product decision).

Anyone with the link can list rooms, see a room's booked slots, create a booking
(instant confirmation + emailed access code), look one up, and cancel it.
"""

from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import EmailStr
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import SessionDep, require_role
from app.core.config import get_settings
from app.models.booking import Booking
from app.models.room import Room
from app.repositories import bookings_repo, rooms_repo
from app.schemas.bookings import (
    AdminBookingsResponse,
    BookingConfirmationResponse,
    BookingCreateRequest,
    BookingDetailResponse,
    BookingOut,
    BookingsListResponse,
    BookingSlotOut,
    BookingStats,
    RoomBookingsResponse,
    RoomDetailResponse,
    RoomOut,
    RoomsListResponse,
)
from app.services import booking_service

router = APIRouter(prefix="/bookings", tags=["bookings"])

_BOOKING_ADMIN_ROLES = ("admin", "superadmin")


def _booking_out(booking: Booking, room: Room | None) -> BookingOut:
    return BookingOut(
        id=booking.id,
        room_id=booking.room_id,
        room_name=room.name if room else None,
        booker_name=booking.booker_name,
        booker_email=booking.booker_email,
        title=booking.title,
        start_time=booking.start_time,
        end_time=booking.end_time,
        access_code=booking.access_code,
        status=booking.status,
        created_at=booking.created_at,
    )


def _room_out(room: Room) -> RoomOut:
    return RoomOut(
        id=room.id,
        name=room.name,
        location=room.location,
        capacity=room.capacity,
        description=room.description,
        room_type=room.room_type,
        size_sqm=room.size_sqm,
        amenities=room.amenities,
        image_url=room.image_url,
    )


async def _rooms_by_id(session: AsyncSession, bookings: list[Booking]) -> dict[int, Room]:
    """Batch-fetch rooms for a list of bookings, including inactive ones."""
    rooms: dict[int, Room] = {}
    for room_id in {b.room_id for b in bookings}:
        room = await rooms_repo.get_room(session, room_id)
        if room is not None:
            rooms[room_id] = room
    return rooms


@router.get("/rooms")
async def list_rooms(session: SessionDep) -> RoomsListResponse:
    rooms = await rooms_repo.list_active_rooms(session)
    return RoomsListResponse(
        status_code=status.HTTP_200_OK, rooms=[_room_out(r) for r in rooms]
    )


@router.get("/rooms/{room_id}")
async def get_room(room_id: int, session: SessionDep) -> RoomDetailResponse:
    room = await rooms_repo.get_room(session, room_id)
    if room is None or not room.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Room not found")
    return RoomDetailResponse(status_code=status.HTTP_200_OK, room=_room_out(room))


@router.get("/rooms/{room_id}/bookings")
async def room_bookings(
    room_id: int,
    session: SessionDep,
    day: Annotated[
        date | None, Query(description="Day to inspect (YYYY-MM-DD); defaults to today")
    ] = None,
) -> RoomBookingsResponse:
    """Confirmed booked slots for a room on a given day (booking timezone)."""
    room = await rooms_repo.get_room(session, room_id)
    if room is None or not room.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Room not found")

    tz = ZoneInfo(get_settings().booking_tz)
    target = day or datetime.now(tz).date()
    day_start = datetime.combine(target, time.min, tzinfo=tz)
    day_end = day_start + timedelta(days=1)

    slots = await bookings_repo.list_for_room_between(session, room_id, day_start, day_end)
    return RoomBookingsResponse(
        status_code=status.HTTP_200_OK,
        room_id=room_id,
        slots=[BookingSlotOut(start_time=s.start_time, end_time=s.end_time) for s in slots],
    )


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_booking(
    body: BookingCreateRequest, session: SessionDep
) -> BookingConfirmationResponse:
    try:
        booking, room, email_sent = await booking_service.create_booking(
            session,
            room_id=body.room_id,
            booker_name=body.booker_name,
            booker_email=body.booker_email,
            start_time=body.start_time,
            end_time=body.end_time,
            title=body.title,
        )
    except booking_service.RoomNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except booking_service.InvalidBookingTimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except booking_service.BookingConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    return BookingConfirmationResponse(
        status_code=status.HTTP_201_CREATED,
        booking=_booking_out(booking, room),
        email_sent=email_sent,
    )


@router.get("/by-email")
async def list_bookings_by_email(
    email: Annotated[EmailStr, Query(description="Booker's email")],
    session: SessionDep,
) -> BookingsListResponse:
    """All bookings (any status) made with this email — the "My Bookings" lookup.

    No auth: the email itself isn't secret, and bookings carry no sensitive data
    beyond room/time/purpose. Declared before `/{access_code}` so it isn't
    swallowed by that route.
    """
    bookings = await bookings_repo.list_by_email(session, email)
    rooms = await _rooms_by_id(session, bookings)
    return BookingsListResponse(
        status_code=status.HTTP_200_OK,
        bookings=[_booking_out(b, rooms.get(b.room_id)) for b in bookings],
    )


@router.get("/admin", dependencies=[Depends(require_role(*_BOOKING_ADMIN_ROLES))])
async def admin_list_bookings(
    session: SessionDep,
    room_id: Annotated[int | None, Query(description="Filter to one room")] = None,
) -> AdminBookingsResponse:
    """All bookings (any status), for the staff admin dashboard."""
    bookings = await bookings_repo.list_all(session, room_id=room_id)
    rooms = await _rooms_by_id(session, bookings)

    tz = ZoneInfo(get_settings().booking_tz)
    today = datetime.now(tz).date()
    day_start = datetime.combine(today, time.min, tzinfo=tz)
    day_end = day_start + timedelta(days=1)
    active_rooms = await rooms_repo.list_active_rooms(session)

    stats = BookingStats(
        today_count=await bookings_repo.count_today(session, day_start, day_end),
        active_now_count=await bookings_repo.count_active_now(session, datetime.now(UTC)),
        total_rooms=len(active_rooms),
        all_time_count=await bookings_repo.count_all_time(session),
    )
    return AdminBookingsResponse(
        status_code=status.HTTP_200_OK,
        bookings=[_booking_out(b, rooms.get(b.room_id)) for b in bookings],
        stats=stats,
    )


@router.get("/{access_code}")
async def get_booking(access_code: str, session: SessionDep) -> BookingDetailResponse:
    booking = await bookings_repo.get_by_access_code(session, access_code.strip())
    if booking is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Booking not found")
    room = await rooms_repo.get_room(session, booking.room_id)
    return BookingDetailResponse(
        status_code=status.HTTP_200_OK, booking=_booking_out(booking, room)
    )


@router.post("/{access_code}/cancel")
async def cancel_booking(access_code: str, session: SessionDep) -> BookingDetailResponse:
    try:
        booking = await booking_service.cancel_booking(session, access_code.strip())
    except booking_service.BookingNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    room = await rooms_repo.get_room(session, booking.room_id)
    return BookingDetailResponse(
        status_code=status.HTTP_200_OK, booking=_booking_out(booking, room)
    )


@router.post(
    "/{booking_id}/admin-cancel",
    dependencies=[Depends(require_role(*_BOOKING_ADMIN_ROLES))],
)
async def admin_cancel_booking(booking_id: int, session: SessionDep) -> BookingDetailResponse:
    """Staff cancel by numeric id (the admin dashboard's Cancel action)."""
    try:
        booking = await booking_service.cancel_booking_by_id(session, booking_id)
    except booking_service.BookingNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    room = await rooms_repo.get_room(session, booking.room_id)
    return BookingDetailResponse(
        status_code=status.HTTP_200_OK, booking=_booking_out(booking, room)
    )
