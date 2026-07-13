"""APScheduler job functions (spec §6B + reference refresh)."""

from starlette.datastructures import State

from app.core.database import session_scope
from app.core.logging import get_logger
from app.core.scheduler import (
    LOCK_KEY_DAILY_SNAPSHOT,
    LOCK_KEY_DEAL_SYNC,
    LOCK_KEY_EMAIL_SYNC,
    LOCK_KEY_LEAD_CRM_SYNC,
    LOCK_KEY_NOG_ACTIVITY_SYNC,
    LOCK_KEY_PACK_DELIVERY,
    LOCK_KEY_REFERENCE_SYNC,
    LOCK_KEY_TASK_SYNC,
    run_with_advisory_lock,
)
from app.freshsales.client import FreshsalesClient
from app.services import (
    daily_snapshot,
    deal_sync,
    email_sync,
    lead_crm_sync,
    nog_activity_sync,
    pack_delivery,
    reference_sync,
    task_sync,
)

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


async def task_sync_job(state: State) -> None:
    async def _run() -> None:
        async with session_scope() as session, FreshsalesClient() as client:
            await task_sync.run_task_sync(session, client)
        logger.info("task sync job done")

    await run_with_advisory_lock(LOCK_KEY_TASK_SYNC, "task_sync", _run)


async def email_sync_job(state: State) -> None:
    async def _run() -> None:
        async with session_scope() as session, FreshsalesClient() as client:
            await email_sync.run_email_sync(session, client)
        logger.info("email sync job done")

    await run_with_advisory_lock(LOCK_KEY_EMAIL_SYNC, "email_sync", _run)


async def daily_snapshot_job(state: State) -> None:
    async def _run() -> None:
        async with session_scope() as session:
            await daily_snapshot.run_daily_snapshot(session)
        logger.info("daily snapshot job done")

    await run_with_advisory_lock(LOCK_KEY_DAILY_SNAPSHOT, "daily_snapshot", _run)


async def lead_crm_sync_job(state: State) -> None:
    async def _run() -> None:
        async with session_scope() as session:
            synced = await lead_crm_sync.sync_pending(session)
        logger.info("lead crm sync job done", synced=synced)

    await run_with_advisory_lock(LOCK_KEY_LEAD_CRM_SYNC, "lead_crm_sync", _run)


async def pack_delivery_job(state: State) -> None:
    async def _run() -> None:
        async with session_scope() as session:
            delivered = await pack_delivery.deliver_pending(session)
        logger.info("pack delivery job done", delivered=delivered)

    await run_with_advisory_lock(LOCK_KEY_PACK_DELIVERY, "pack_delivery", _run)


async def nog_activity_sync_job(state: State) -> None:
    async def _run() -> None:
        async with session_scope() as session, FreshsalesClient() as client:
            counters = await nog_activity_sync.run_nog_activity_sync(session, client)
        logger.info("nog activity sync job done", **counters)

    await run_with_advisory_lock(LOCK_KEY_NOG_ACTIVITY_SYNC, "nog_activity_sync", _run)
