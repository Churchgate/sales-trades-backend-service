from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from app.core.database import ping_database

router = APIRouter(tags=["health"])


class HealthStatus(BaseModel):
    status: str


@router.get("/healthz")
async def liveness() -> HealthStatus:
    return HealthStatus(status="ok")


@router.get("/readyz")
async def readiness() -> HealthStatus:
    if not await ping_database():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="database unavailable"
        )
    return HealthStatus(status="ok")
