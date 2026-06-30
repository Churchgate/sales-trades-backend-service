"""Logging-agent webhook: the n8n workflow (Telegram → OpenRouter extraction) POSTs
a structured update here, and the backend writes the Note/Task to Freshsales — scoped
to the allowed (test) pipeline. Auth is a shared secret header, mirroring the
Freshsales webhook in `webhooks.py`. Mounted without the `/api/v1` prefix.
"""

from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, status

from app.core.config import get_settings
from app.freshsales.client import FreshsalesClient
from app.schemas.agent import AgentLogRequest, AgentLogResponse
from app.services import agent_logging

router = APIRouter(prefix="/webhooks/agent", tags=["agent"])


@router.post("/log")
async def agent_log(
    body: AgentLogRequest,
    x_agent_secret: Annotated[str | None, Header()] = None,
) -> AgentLogResponse:
    """Log a rep's update to Freshsales (Note and/or Task) on a test-pipeline deal."""
    settings = get_settings()
    if not settings.agent_webhook_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Agent webhook is not configured",
        )
    if x_agent_secret != settings.agent_webhook_secret:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid agent secret"
        )

    try:
        async with FreshsalesClient() as client:
            result = await agent_logging.log_activity(
                client,
                intent=body.intent,
                deal_id=body.deal_id,
                deal_hint=body.deal_hint,
                note_text=body.note_text,
                task_title=body.task_title,
                due_date=body.due_date,
                owner_id=body.owner_id,
            )
    except agent_logging.PipelineNotAllowedError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except agent_logging.DealNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    return AgentLogResponse(
        deal_id=result.deal_id,
        deal_name=result.deal_name,
        note_id=result.note_id,
        task_id=result.task_id,
        confirmation=result.confirmation,
    )
