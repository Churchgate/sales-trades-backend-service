from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.dependencies import CurrentUserDep, SessionDep, require_role
from app.core.security import generate_temp_password, hash_password
from app.freshsales.client import FreshsalesClient
from app.models.dashboard_user import DashboardUser
from app.repositories import events_repo, users_repo
from app.schemas.auth import CreateUserRequest, CurrentUser
from app.schemas.responses import MessageResponse, UserCreatedResponse, UsersListResponse
from app.services import (
    daily_snapshot,
    deal_sync,
    email_sync,
    mailer,
    reference_sync,
    task_sync,
    timeline_backfill,
)

router = APIRouter(prefix="/admin", tags=["admin"])


@router.post("/sync/reference", dependencies=[Depends(require_role("admin", "superadmin"))])
async def trigger_reference_sync(request: Request, session: SessionDep) -> MessageResponse:
    async with FreshsalesClient() as client:
        request.app.state.stage_resolver = await reference_sync.run_reference_sync(
            session, client
        )
    return MessageResponse(status_code=status.HTTP_200_OK, message="Reference sync complete")


@router.post("/sync/deals", dependencies=[Depends(require_role("admin", "superadmin"))])
async def trigger_deal_sync(session: SessionDep) -> MessageResponse:
    async with FreshsalesClient() as client:
        await deal_sync.run_deal_sync(session, client)
    return MessageResponse(status_code=status.HTTP_200_OK, message="Deal sync complete")


@router.post("/sync/tasks", dependencies=[Depends(require_role("admin", "superadmin"))])
async def trigger_task_sync(session: SessionDep) -> MessageResponse:
    async with FreshsalesClient() as client:
        await task_sync.run_task_sync(session, client)
    return MessageResponse(status_code=status.HTTP_200_OK, message="Task sync complete")


@router.post("/sync/emails", dependencies=[Depends(require_role("admin", "superadmin"))])
async def trigger_email_sync(session: SessionDep) -> MessageResponse:
    async with FreshsalesClient() as client:
        await email_sync.run_email_sync(session, client)
    return MessageResponse(status_code=status.HTTP_200_OK, message="Email sync complete")


@router.post("/snapshot/daily", dependencies=[Depends(require_role("admin", "superadmin"))])
async def trigger_daily_snapshot(session: SessionDep) -> MessageResponse:
    """Roll up today's pipeline_daily_snapshot now (otherwise runs nightly)."""
    await daily_snapshot.run_daily_snapshot(session)
    return MessageResponse(status_code=status.HTTP_200_OK, message="Daily snapshot complete")


@router.post("/backfill/timeline", dependencies=[Depends(require_role("admin", "superadmin"))])
async def trigger_timeline_backfill(session: SessionDep) -> MessageResponse:
    """Seed deal_events history for deals that have none yet (spec §6C)."""
    deal_ids = await events_repo.list_deal_ids_without_events(session)
    async with FreshsalesClient() as client:
        await timeline_backfill.run_timeline_backfill(session, client, deal_ids)
    return MessageResponse(
        status_code=status.HTTP_200_OK,
        message=f"Timeline backfill complete ({len(deal_ids)} deals)",
    )


@router.post("/users", dependencies=[Depends(require_role("superadmin"))],
             status_code=status.HTTP_201_CREATED)
async def create_user(body: CreateUserRequest, session: SessionDep) -> UserCreatedResponse:
    """Invite a dashboard user. A temporary password is generated (unless one is
    supplied), the user is flagged to change it on first login, and an invite email
    with the login link is sent. The temp password is also returned so the admin can
    pass it on directly when email delivery isn't configured."""
    existing = await users_repo.get_user_by_email(session, body.email)
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

    temp_password = body.password or generate_temp_password()
    user = DashboardUser(
        email=body.email,
        role=body.role,
        owner_id=body.owner_id,
        hashed_password=hash_password(temp_password),
        must_change_password=True,
    )
    await users_repo.create_user(session, user)
    email_sent = await mailer.send_invite_email(user.email, temp_password)

    return UserCreatedResponse(
        status_code=status.HTTP_201_CREATED,
        user=CurrentUser(
            email=user.email, role=user.role, owner_id=user.owner_id,
            must_change_password=user.must_change_password,
        ),
        temp_password=temp_password,
        email_sent=email_sent,
    )


@router.post("/users/{email}/reset-password",
             dependencies=[Depends(require_role("superadmin"))])
async def reset_user_password(email: str, session: SessionDep) -> UserCreatedResponse:
    """Issue a fresh temporary password for an existing user and re-flag them to
    change it on next login. Emails the new password and also returns it."""
    user = await users_repo.get_user_by_email(session, email)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    temp_password = generate_temp_password()
    await users_repo.set_password(
        session, user, hash_password(temp_password), must_change=True
    )
    email_sent = await mailer.send_invite_email(user.email, temp_password)

    return UserCreatedResponse(
        status_code=status.HTTP_200_OK,
        user=CurrentUser(
            email=user.email, role=user.role, owner_id=user.owner_id,
            must_change_password=user.must_change_password,
        ),
        temp_password=temp_password,
        email_sent=email_sent,
    )


@router.delete("/users/{email}", dependencies=[Depends(require_role("superadmin"))])
async def delete_user(
    email: str, session: SessionDep, current_user: CurrentUserDep
) -> MessageResponse:
    """Remove a dashboard user — works whether they're a pending invite or active.
    A superadmin can't delete their own account (avoids locking out the last admin)."""
    if email.lower() == current_user.email.lower():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="You can't delete your own account"
        )
    user = await users_repo.get_user_by_email(session, email)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    await users_repo.delete_user(session, user)
    return MessageResponse(status_code=status.HTTP_200_OK, message="User deleted")


@router.get("/users", dependencies=[Depends(require_role("superadmin"))])
async def list_users(session: SessionDep) -> UsersListResponse:
    users = await users_repo.list_users(session)
    return UsersListResponse(
        status_code=status.HTTP_200_OK,
        users=[
            CurrentUser(
                email=u.email, role=u.role, owner_id=u.owner_id,
                must_change_password=u.must_change_password,
            )
            for u in users
        ],
    )
