from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.dependencies import SessionDep, require_role
from app.core.security import hash_password
from app.freshsales.client import FreshsalesClient
from app.models.dashboard_user import DashboardUser
from app.repositories import events_repo, users_repo
from app.schemas.auth import CreateUserRequest, CurrentUser
from app.schemas.responses import MessageResponse, UserCreatedResponse, UsersListResponse
from app.services import deal_sync, email_sync, reference_sync, task_sync, timeline_backfill

router = APIRouter(prefix="/admin", tags=["admin"])


@router.post("/sync/reference", dependencies=[Depends(require_role("gmd", "superadmin"))])
async def trigger_reference_sync(request: Request, session: SessionDep) -> MessageResponse:
    async with FreshsalesClient() as client:
        request.app.state.stage_resolver = await reference_sync.run_reference_sync(
            session, client
        )
    return MessageResponse(status_code=status.HTTP_200_OK, message="Reference sync complete")


@router.post("/sync/deals", dependencies=[Depends(require_role("gmd", "superadmin"))])
async def trigger_deal_sync(session: SessionDep) -> MessageResponse:
    async with FreshsalesClient() as client:
        await deal_sync.run_deal_sync(session, client)
    return MessageResponse(status_code=status.HTTP_200_OK, message="Deal sync complete")


@router.post("/sync/tasks", dependencies=[Depends(require_role("gmd", "superadmin"))])
async def trigger_task_sync(session: SessionDep) -> MessageResponse:
    async with FreshsalesClient() as client:
        await task_sync.run_task_sync(session, client)
    return MessageResponse(status_code=status.HTTP_200_OK, message="Task sync complete")


@router.post("/sync/emails", dependencies=[Depends(require_role("gmd", "superadmin"))])
async def trigger_email_sync(session: SessionDep) -> MessageResponse:
    async with FreshsalesClient() as client:
        await email_sync.run_email_sync(session, client)
    return MessageResponse(status_code=status.HTTP_200_OK, message="Email sync complete")


@router.post("/backfill/timeline", dependencies=[Depends(require_role("gmd", "superadmin"))])
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
    existing = await users_repo.get_user_by_email(session, body.email)
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")
    user = DashboardUser(
        email=body.email,
        role=body.role,
        owner_id=body.owner_id,
        hashed_password=hash_password(body.password),
    )
    await users_repo.create_user(session, user)
    return UserCreatedResponse(
        status_code=status.HTTP_201_CREATED,
        user=CurrentUser(email=user.email, role=user.role, owner_id=user.owner_id),
    )


@router.get("/users", dependencies=[Depends(require_role("superadmin"))])
async def list_users(session: SessionDep) -> UsersListResponse:
    users = await users_repo.list_users(session)
    return UsersListResponse(
        status_code=status.HTTP_200_OK,
        users=[CurrentUser(email=u.email, role=u.role, owner_id=u.owner_id) for u in users],
    )
