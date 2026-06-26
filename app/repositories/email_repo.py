from typing import Any

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.email_activity import EmailActivity


async def replace_deal_emails(
    session: AsyncSession, deal_id: int, rows: list[dict[str, Any]]
) -> None:
    """Replace all email_activity rows for one deal with a fresh set.

    `email_activity` has no stable external key to upsert on, so a per-deal
    delete-then-insert keeps the sync idempotent (re-running never duplicates).
    """
    await session.execute(delete(EmailActivity).where(EmailActivity.deal_id == deal_id))
    for row in rows:
        session.add(EmailActivity(**row))
