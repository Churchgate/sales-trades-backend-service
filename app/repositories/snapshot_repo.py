from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def write_today_snapshot(session: AsyncSession) -> int:
    """Roll up the current `deals_snapshot` into `pipeline_daily_snapshot` for today,
    by (pipeline_id, stage_id). Idempotent for same-day re-runs via the date+pipeline+
    stage primary key (spec §B3). Returns the number of rows written."""
    result = await session.execute(
        text("""
            INSERT INTO pipeline_daily_snapshot
                (snapshot_date, pipeline_id, stage_id,
                 deal_count, total_value, total_base_currency_value)
            SELECT
                current_date, pipeline_id, stage_id,
                count(*),
                coalesce(sum(amount), 0),
                coalesce(sum(base_currency_amount), 0)
            FROM deals_snapshot
            WHERE pipeline_id IS NOT NULL AND stage_id IS NOT NULL
            GROUP BY pipeline_id, stage_id
            ON CONFLICT (snapshot_date, pipeline_id, stage_id) DO UPDATE SET
                deal_count = EXCLUDED.deal_count,
                total_value = EXCLUDED.total_value,
                total_base_currency_value = EXCLUDED.total_base_currency_value
        """)
    )
    return result.rowcount
