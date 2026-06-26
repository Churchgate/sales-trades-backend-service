"""APScheduler wiring with Postgres advisory-lock job de-duplication.

Each scheduled job acquires `pg_try_advisory_lock` before running so a
multi-instance cloud deployment never runs the same job concurrently on more
than one instance.
"""

from collections.abc import Awaitable, Callable

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import text

from app.core.database import session_scope
from app.core.logging import get_logger

logger = get_logger(__name__)

# Arbitrary, stable advisory-lock keys (bigint) - one per scheduled job.
LOCK_KEY_REFERENCE_SYNC = 17_000_001
LOCK_KEY_DEAL_SYNC = 17_000_002
LOCK_KEY_TASK_SYNC = 17_000_003
LOCK_KEY_EMAIL_SYNC = 17_000_004
LOCK_KEY_DAILY_SNAPSHOT = 17_000_005
LOCK_KEY_LEAD_CRM_SYNC = 17_000_006


async def run_with_advisory_lock(
    lock_key: int, job_name: str, fn: Callable[[], Awaitable[None]]
) -> None:
    async with session_scope() as session:
        acquired = (
            await session.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": lock_key})
        ).scalar_one()

        if not acquired:
            logger.info("job skipped, lock held elsewhere", job=job_name)
            return

        try:
            await fn()
        except Exception:
            logger.exception("job failed", job=job_name)
            raise
        finally:
            await session.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": lock_key})


def create_scheduler() -> AsyncIOScheduler:
    return AsyncIOScheduler()
