from fastapi import APIRouter, Depends, status

from app.api.dependencies import SessionDep, get_current_user
from app.repositories import analytics_repo
from app.schemas.responses import DataFreshnessResponse

# Authentication is shared across every analytics route at the router level; the
# read endpoints add owner scoping (OwnerScopeDep) per route as they are built out.
router = APIRouter(
    prefix="/analytics",
    tags=["analytics"],
    dependencies=[Depends(get_current_user)],
)


@router.get("/data-freshness")
async def data_freshness(session: SessionDep) -> DataFreshnessResponse:
    """Last-run timestamps per ingestion source — backs the "data as of" banner."""
    freshness = await analytics_repo.get_data_freshness(session)
    return DataFreshnessResponse(
        status_code=status.HTTP_200_OK,
        data_as_of=freshness.data_as_of,
        deals_synced_at=freshness.deals_synced_at,
        reference_synced_at=freshness.reference_synced_at,
        tasks_synced_at=freshness.tasks_synced_at,
        email_last_activity_at=freshness.email_last_activity_at,
    )
