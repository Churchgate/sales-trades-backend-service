"""APScheduler job functions (spec §6B + reference refresh)."""

from starlette.datastructures import State

from app.core.database import session_scope
from app.core.logging import get_logger
from app.core.scheduler import LOCK_KEY_DEAL_SYNC, LOCK_KEY_REFERENCE_SYNC, run_with_advisory_lock
from app.freshsales.client import FreshsalesClient
from app.services import deal_sync, reference_sync

logger = get_logger(__name__)


async def reference_sync_job(state: State) -> None:
    async def _run() -> None:
        async with session_scope() as session, FreshsalesClient() as client:
            state.stage_resolver = await reference_sync.run_reference_sync(session, client)
        logger.info("reference sync job done")

    await run_with_advisory_lock(LOCK_KEY_REFERENCE_SYNC, "reference_sync", _run)


async def deal_sync_job(state: State) -> None:
    async def _run() -> None:
        async with session_scope() as session, FreshsalesClient() as client:
            await deal_sync.run_deal_sync(session, client)
        logger.info("deal sync job done")

    await run_with_advisory_lock(LOCK_KEY_DEAL_SYNC, "deal_sync", _run)
