"""Tasks sync (spec §6E): mirror Freshsales deal tasks into `tasks_snapshot`.

Scoped to *open* deals only to stay within the Freshsales rate limit (spec §7) —
closed deals don't need follow-up tracking. Powers the "overdue follow-up tasks per
rep" alert (Vinay's #1 priority) and the next-action discipline metrics.
"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.freshsales.client import FreshsalesClient
from app.freshsales.parsing import parse_iso_timestamp
from app.repositories import deals_repo, tasks_repo

logger = get_logger(__name__)


def _task_status(task: dict[str, Any]) -> str:
    """Normalise a Freshsales task's completion to 'open' | 'completed'.

    Verified live: open tasks have `status: 0` and `completed_date: null`. A present
    `completed_date` is the most reliable completion signal; `status` truthiness is a
    secondary guard.
    """
    if task.get("completed_date") or task.get("status") in (1, "1", 2, "2", "completed"):
        return "completed"
    return "open"


def _parse_task(task: dict[str, Any], deal_id: int) -> dict[str, Any]:
    status = _task_status(task)
    return {
        "task_id": task["id"],
        "deal_id": deal_id,
        "owner_id": task.get("owner_id"),
        "title": task.get("title"),
        "status": status,
        "due_date": parse_iso_timestamp(task.get("due_date")),
        "completed_date": parse_iso_timestamp(task.get("completed_date"))
        if status == "completed"
        else None,
    }


async def run_task_sync(session: AsyncSession, client: FreshsalesClient) -> None:
    """Upsert `tasks_snapshot` from each open deal's task list."""
    open_deal_ids = await deals_repo.list_open_deal_ids(session)
    counters = {"tasks": 0, "deals": 0}

    for deal_id in open_deal_ids:
        body = await client.get_deal_tasks(deal_id)
        for task in body.get("tasks", []):
            await tasks_repo.upsert_task(session, _parse_task(task, deal_id))
            counters["tasks"] += 1
        counters["deals"] += 1
        await session.commit()

    logger.info("task sync complete", deals=counters["deals"], tasks=counters["tasks"])
