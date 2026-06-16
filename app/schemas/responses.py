from pydantic import BaseModel

from app.schemas.auth import CurrentUser


class BaseResponse(BaseModel):
    status: str = "success"
    status_code: int


class AuthResponse(BaseResponse):
    """Login and refresh — tokens in body (for API clients) and in httpOnly cookies."""
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: CurrentUser


class MeResponse(BaseResponse):
    user: CurrentUser


class UserCreatedResponse(BaseResponse):
    user: CurrentUser


class UsersListResponse(BaseResponse):
    users: list[CurrentUser]


class MessageResponse(BaseResponse):
    message: str
