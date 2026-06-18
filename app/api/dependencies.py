from collections.abc import Awaitable, Callable
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import APIKeyCookie
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.security import decode_token
from app.freshsales.parsing import PipelineStageResolver
from app.models.dashboard_user import DashboardUser
from app.repositories import users_repo

SessionDep = Annotated[AsyncSession, Depends(get_session)]

# Declares the access_token cookie as an OpenAPI security scheme so Swagger
# renders the padlock icon instead of a plain cookie parameter field.
_cookie_scheme = APIKeyCookie(name="access_token", auto_error=False)


def get_resolver(request: Request) -> PipelineStageResolver:
    resolver = getattr(request.app.state, "stage_resolver", None)
    if resolver is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Reference data not loaded yet",
        )
    return resolver


ResolverDep = Annotated[PipelineStageResolver, Depends(get_resolver)]


async def get_current_user(
    session: SessionDep,
    access_token: Annotated[str | None, Depends(_cookie_scheme)] = None,
) -> DashboardUser:
    if access_token is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    try:
        payload = decode_token(access_token)
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token"
        ) from exc

    if payload.get("type") != "access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token type")

    user = await users_repo.get_user_by_email(session, payload["sub"])
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unknown user")
    return user


CurrentUserDep = Annotated[DashboardUser, Depends(get_current_user)]


def require_role(*roles: str) -> Callable[[DashboardUser], Awaitable[DashboardUser]]:
    async def _check(user: CurrentUserDep) -> DashboardUser:
        if user.role not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role")
        return user

    return _check


def get_owner_scope(user: CurrentUserDep) -> int | None:
    """Owner-id filter for analytics queries. `rep` users are restricted to their own
    linked Freshsales owner; `gmd`/`sales_manager`/`superadmin` see everything (`None`).
    A rep with no linked owner gets 403 rather than silently seeing all deals."""
    if user.role == "rep":
        if user.owner_id is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Rep account is not linked to a Freshsales owner",
            )
        return user.owner_id
    return None


OwnerScopeDep = Annotated[int | None, Depends(get_owner_scope)]
