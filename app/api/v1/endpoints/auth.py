import jwt
from fastapi import APIRouter, HTTPException, Request, Response, status

from app.api.dependencies import CurrentUserDep, SessionDep
from app.core.config import get_settings
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.repositories import users_repo
from app.schemas.auth import ChangePasswordRequest, CurrentUser, LoginRequest
from app.schemas.responses import AuthResponse, MeResponse, MessageResponse

router = APIRouter(prefix="/auth", tags=["auth"])

_ACCESS_COOKIE = "access_token"
_REFRESH_COOKIE = "refresh_token"


def _set_auth_cookies(response: Response, access_token: str, refresh_token: str) -> None:
    settings = get_settings()
    secure = settings.environment != "development"
    response.set_cookie(
        _ACCESS_COOKIE, access_token, httponly=True, secure=secure, samesite="lax",
        max_age=settings.jwt_access_token_expire_minutes * 60,
    )
    response.set_cookie(
        _REFRESH_COOKIE, refresh_token, httponly=True, secure=secure, samesite="lax",
        max_age=settings.jwt_refresh_token_expire_minutes * 60,
    )


@router.post("/login")
async def login(body: LoginRequest, response: Response, session: SessionDep) -> AuthResponse:
    user = await users_repo.get_user_by_email(session, body.email)
    if user is None or not verify_password(body.password, user.hashed_password or ""):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    access_token = create_access_token(user.email, user.role)
    refresh_token = create_refresh_token(user.email, user.role)
    _set_auth_cookies(response, access_token, refresh_token)

    return AuthResponse(
        status_code=status.HTTP_200_OK,
        access_token=access_token,
        refresh_token=refresh_token,
        user=CurrentUser(
            email=user.email, role=user.role, owner_id=user.owner_id,
            must_change_password=user.must_change_password,
        ),
    )


@router.get("/me")
async def me(user: CurrentUserDep) -> MeResponse:
    return MeResponse(
        status_code=status.HTTP_200_OK,
        user=CurrentUser(
            email=user.email, role=user.role, owner_id=user.owner_id,
            must_change_password=user.must_change_password,
        ),
    )


@router.post("/refresh")
async def refresh(request: Request, response: Response, session: SessionDep) -> AuthResponse:
    refresh_token = request.cookies.get(_REFRESH_COOKIE)
    if refresh_token is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    try:
        payload = decode_token(refresh_token)
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token"
        ) from exc

    if payload.get("type") != "refresh":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token type")

    user = await users_repo.get_user_by_email(session, payload["sub"])
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unknown user")

    access_token = create_access_token(user.email, user.role)
    new_refresh_token = create_refresh_token(user.email, user.role)
    _set_auth_cookies(response, access_token, new_refresh_token)

    return AuthResponse(
        status_code=status.HTTP_200_OK,
        access_token=access_token,
        refresh_token=new_refresh_token,
        user=CurrentUser(
            email=user.email, role=user.role, owner_id=user.owner_id,
            must_change_password=user.must_change_password,
        ),
    )


@router.post("/change-password")
async def change_password(
    body: ChangePasswordRequest, user: CurrentUserDep, session: SessionDep
) -> MessageResponse:
    """Replace the current user's password (e.g. the temporary one from an invite).
    Verifies the current password, then stores the new one and clears the
    must-change flag."""
    if not verify_password(body.current_password, user.hashed_password or ""):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Current password is incorrect"
        )
    if body.new_password == body.current_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New password must be different from the current one",
        )
    await users_repo.set_password(
        session, user, hash_password(body.new_password), must_change=False
    )
    return MessageResponse(status_code=status.HTTP_200_OK, message="Password updated")


@router.post("/logout")
async def logout(response: Response) -> MessageResponse:
    response.delete_cookie(_ACCESS_COOKIE)
    response.delete_cookie(_REFRESH_COOKIE)
    return MessageResponse(status_code=status.HTTP_200_OK, message="Logged out successfully")
