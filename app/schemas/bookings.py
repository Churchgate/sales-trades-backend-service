from datetime import datetime

from pydantic import BaseModel, EmailStr, Field

from app.schemas.responses import BaseResponse

# --- Requests ---


class BookingCreateRequest(BaseModel):
    room_id: int
    booker_name: str = Field(min_length=1, max_length=200)
    booker_email: EmailStr
    title: str | None = Field(default=None, max_length=300)
    start_time: datetime
    end_time: datetime


# --- Read models ---


class RoomOut(BaseModel):
    id: int
    name: str
    location: str | None = None
    capacity: int | None = None
    description: str | None = None


class BookingSlotOut(BaseModel):
    """A booked slot, exposed for availability rendering (no personal details)."""

    start_time: datetime
    end_time: datetime


class BookingOut(BaseModel):
    id: int
    room_id: int
    room_name: str | None = None
    booker_name: str
    booker_email: EmailStr
    title: str | None = None
    start_time: datetime
    end_time: datetime
    access_code: str
    status: str


# --- Responses (envelope-wrapped, like the rest of the API) ---


class RoomsListResponse(BaseResponse):
    rooms: list[RoomOut]


class RoomBookingsResponse(BaseResponse):
    room_id: int
    slots: list[BookingSlotOut]


class BookingConfirmationResponse(BaseResponse):
    booking: BookingOut
    email_sent: bool


class BookingDetailResponse(BaseResponse):
    booking: BookingOut
