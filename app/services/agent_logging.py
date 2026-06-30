"""Logging-agent write path: resolve a deal, enforce the pipeline allow-list, then
create a Note and/or Task in Freshsales.

The pipeline guard is the safety rail for the test phase — writes are refused unless
the target deal is in `Settings.agent_allowed_pipelines` (locked to the Test pipeline
by default), so a misrouted message can never touch a real deal.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.core.config import get_settings
from app.core.logging import get_logger
from app.freshsales.client import FreshsalesClient

logger = get_logger(__name__)

_LAGOS = ZoneInfo("Africa/Lagos")
_VALID_INTENTS = {"note", "task", "note+task"}


class PipelineNotAllowedError(Exception):
    """The resolved deal is not in an allowed pipeline — the write is refused."""


class DealNotFoundError(Exception):
    """Could not resolve the deal from the given id/hint."""


@dataclass
class LogResult:
    deal_id: int
    deal_name: str
    note_id: int | None
    task_id: int | None
    confirmation: str


def _resolve_due(due_date: str | None) -> str:
    """Normalise a due date to ISO-8601 with offset. Accepts `YYYY-MM-DD` or a full
    datetime; a bare date becomes 17:00 Africa/Lagos. Defaults to tomorrow 17:00."""
    if due_date:
        try:
            d = datetime.fromisoformat(due_date)
        except ValueError:
            d = datetime.fromisoformat(f"{due_date}T17:00:00")
        if d.tzinfo is None:
            d = d.replace(tzinfo=_LAGOS)
    else:
        d = (datetime.now(_LAGOS) + timedelta(days=1)).replace(
            hour=17, minute=0, second=0, microsecond=0
        )
    return d.isoformat()


def _confirmation(
    deal_name: str, note_id: int | None, task_title: str | None,
    task_id: int | None, due_date: str | None,
) -> str:
    parts: list[str] = []
    if note_id is not None:
        parts.append("note")
    if task_id is not None:
        parts.append(f'task "{task_title}"' + (f" due {due_date}" if due_date else ""))
    what = " + ".join(parts) if parts else "nothing"
    return f'✅ Logged on "{deal_name}": {what}.'


async def _resolve_deal(
    client: FreshsalesClient, *, deal_id: int | None, deal_hint: str | None,
    allowed: set[int],
) -> dict:
    """Resolve the deal by id (preferred) or by a name/account contains-match within
    the allowed pipelines. Raises DealNotFoundError if nothing matches."""
    if deal_id is not None:
        deal = await client.get_deal(deal_id)
        if not deal or "id" not in deal:
            raise DealNotFoundError(f"deal {deal_id} not found")
        return deal
    if deal_hint:
        hint = deal_hint.lower()
        for pid in allowed:
            async for did in client.iter_pipeline_deal_ids(pid):
                d = await client.get_deal(did)
                name = (d.get("name") or "").lower()
                account = (d.get("sales_account_name") or "").lower()
                if hint in name or (account and hint in account):
                    return d
        raise DealNotFoundError(f"no deal in allowed pipelines matches {deal_hint!r}")
    raise DealNotFoundError("provide deal_id or deal_hint")


async def log_activity(
    client: FreshsalesClient, *, intent: str, deal_id: int | None = None,
    deal_hint: str | None = None, note_text: str | None = None,
    task_title: str | None = None, due_date: str | None = None,
    owner_id: int | None = None,
) -> LogResult:
    """Resolve the deal, enforce the pipeline allow-list, then create the Note/Task."""
    if intent not in _VALID_INTENTS:
        raise ValueError(f"intent must be one of {sorted(_VALID_INTENTS)}")

    allowed = get_settings().agent_allowed_pipelines
    deal = await _resolve_deal(client, deal_id=deal_id, deal_hint=deal_hint, allowed=allowed)
    did = deal["id"]
    pipeline_id = deal.get("deal_pipeline_id")
    if pipeline_id not in allowed:
        raise PipelineNotAllowedError(
            f"deal {did} is in pipeline {pipeline_id}, not in allowed {sorted(allowed)}"
        )

    deal_name = deal.get("name") or str(did)
    task_owner = owner_id or deal.get("owner_id")

    note_id: int | None = None
    task_id: int | None = None
    if intent in {"note", "note+task"} and note_text:
        note = await client.create_note(did, note_text)
        note_id = note.get("id")
    if intent in {"task", "note+task"} and task_title:
        task = await client.create_task(
            did, task_title, due_date=_resolve_due(due_date), owner_id=task_owner
        )
        task_id = task.get("id")

    logger.info(
        "agent activity logged", deal_id=did, pipeline_id=pipeline_id,
        note_id=note_id, task_id=task_id,
    )
    return LogResult(
        deal_id=did, deal_name=deal_name, note_id=note_id, task_id=task_id,
        confirmation=_confirmation(deal_name, note_id, task_title, task_id, due_date),
    )
