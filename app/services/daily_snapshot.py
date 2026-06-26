"""Daily pipeline-snapshot rollup (spec §B3).

Writes one `pipeline_daily_snapshot` row per (pipeline, stage) per day so the trends
endpoint can show week-over-week movement. Pure DB rollup — no Freshsales calls.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.repositories import snapshot_repo

logger = get_logger(__name__)


async def run_daily_snapshot(session: AsyncSession) -> None:
    rows = await snapshot_repo.write_today_snapshot(session)
    await session.commit()
    logger.info("daily snapshot complete", rows=rows)
