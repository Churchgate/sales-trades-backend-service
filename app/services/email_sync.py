"""Email-activity sync (spec §6D): mirror Freshsales deal conversations into
`email_activity`.

Scoped to *open* deals only (rate limit, spec §7). Enables the first-response-time
metric: first `direction='outgoing'` `conversation_time` minus `deal_created_at`
("time to first outreach"), computed downstream in the analytics layer.
"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.freshsales.client import FreshsalesClient
from app.freshsales.parsing import parse_iso_timestamp
from app.repositories import deals_repo, email_repo

logger = get_logger(__name__)

# Bulky fields on a conversation record (full email bodies / attachments) that we
# don't need for analytics — stripped before persisting to keep raw_payload small.
_HEAVY_EMAIL_FIELDS = frozenset(
    {
        "html_content",
        "current_html_content",
        "display_content",
        "shrink_content",
        "attachments",
        "conversation_meta",
    }
)


def _parse_email(conversation: dict[str, Any], deal_id: int) -> dict[str, Any] | None:
    """Map one email conversation onto `email_activity` columns.

    Drops conversations without a parseable timestamp — without it the row can't
    contribute to response-time or last-activity calculations. `raw_payload` keeps
    the record's metadata but strips full email bodies (verified live: each record
    carries several large HTML content fields).
    """
    conversation_time = parse_iso_timestamp(conversation.get("conversation_time"))
    if conversation_time is None:
        return None
    raw = {k: v for k, v in conversation.items() if k not in _HEAVY_EMAIL_FIELDS}
    return {
        "deal_id": deal_id,
        "direction": str(conversation.get("direction") or "unknown"),
        "conversation_time": conversation_time,
        "subject": conversation.get("subject"),
        "raw_payload": raw,
    }


async def run_email_sync(session: AsyncSession, client: FreshsalesClient) -> None:
    """Replace `email_activity` from each open deal's conversation list."""
    open_deal_ids = await deals_repo.list_open_deal_ids(session)
    counters = {"emails": 0, "deals": 0}

    for deal_id in open_deal_ids:
        body = await client.get_deal_conversations(deal_id)
        rows = [
            row
            for conversation in body.get("email_conversations", [])
            if (row := _parse_email(conversation, deal_id)) is not None
        ]
        await email_repo.replace_deal_emails(session, deal_id, rows)
        counters["emails"] += len(rows)
        counters["deals"] += 1
        await session.commit()

    logger.info("email sync complete", deals=counters["deals"], emails=counters["emails"])
