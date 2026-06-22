"""Public hall/boardroom booking API (no authentication, per product decision).

Anyone with the link can list rooms, see a room's booked slots, create a booking
(instant confirmation + emailed access code), look one up, and cancel it.
"""

from datetime import date, datetime, time, timedelta
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query, status

from app.api.dependencies import SessionDep
from app.core.config import get_settings
from app.models.booking import Booking
from app.models.room import Room
from app.repositories import bookings_repo, rooms_repo
from app.schemas.bookings import (
    BookingConfirmationResponse,
    BookingCreateRequest,
    BookingDetailResponse,
    BookingOut,
    BookingSlotOut,
    RoomBookingsResponse,
    RoomOut,
    RoomsListResponse,
)
from app.services import booking_service

router = APIRouter(prefix="/bookings", tags=["bookings"])


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
    )


@router.get("/rooms")
async def list_rooms(session: SessionDep) -> RoomsListResponse:
    rooms = await rooms_repo.list_active_rooms(session)
    return RoomsListResponse(
        status_code=status.HTTP_200_OK,
        rooms=[
            RoomOut(
                id=r.id,
                name=r.name,
                location=r.location,
                capacity=r.capacity,
                description=r.description,
            )
            for r in rooms
        ],
    )


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
