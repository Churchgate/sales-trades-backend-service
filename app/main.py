from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.endpoints import health, webhooks
from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.database import session_scope
from app.core.logging import configure_logging, get_logger
from app.core.scheduler import create_scheduler
from app.freshsales.client import FreshsalesClient
from app.jobs.tasks import (
    daily_snapshot_job,
    deal_sync_job,
    email_sync_job,
    reference_sync_job,
    task_sync_job,
)
from app.services import reference_sync

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging()

    app.state.stage_resolver = None
    if settings.sync_on_startup:
        try:
            async with session_scope() as session, FreshsalesClient() as client:
                app.state.stage_resolver = await reference_sync.run_reference_sync(session, client)
            logger.info("startup reference sync complete")
        except Exception:
            logger.exception(
                "startup reference sync failed; webhook ingestion will 503 until a sync succeeds"
            )
    else:
        logger.info("startup reference sync skipped (sync_on_startup=false)")

    scheduler = create_scheduler()
    if settings.run_scheduler:
        scheduler.add_job(
            reference_sync_job,
            "interval",
            hours=settings.reference_sync_interval_hours,
            kwargs={"state": app.state},
            id="reference_sync",
        )
        scheduler.add_job(
            deal_sync_job,
            "interval",
            minutes=settings.deal_sync_interval_minutes,
            kwargs={"state": app.state},
            id="deal_sync",
        )
        scheduler.add_job(
            task_sync_job,
            "interval",
            minutes=settings.activity_sync_interval_minutes,
            kwargs={"state": app.state},
            id="task_sync",
        )
        scheduler.add_job(
            email_sync_job,
            "interval",
            minutes=settings.activity_sync_interval_minutes,
            kwargs={"state": app.state},
            id="email_sync",
        )
        scheduler.add_job(
            daily_snapshot_job,
            "cron",
            hour=1,
            minute=0,
            kwargs={"state": app.state},
            id="daily_snapshot",
        )
        scheduler.start()
        logger.info("scheduler started")

    yield

    if settings.run_scheduler:
        scheduler.shutdown()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Churchgate Dashboard API", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        # Sales dashboard (cookie auth) + the standalone booking frontend (public API).
        allow_origins=[settings.frontend_base_url, settings.booking_frontend_base_url],
        allow_credentials=True,  # required for cookies
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router)
    app.include_router(webhooks.router)
    app.include_router(api_router, prefix="/api/v1")
    return app


app = create_app()
