from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException, status

from app.api.dependencies import ResolverDep, SessionDep
from app.core.config import get_settings
from app.services.webhook_ingest import ingest_webhook

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/freshsales/deal", status_code=status.HTTP_202_ACCEPTED)
async def freshsales_deal_webhook(
    payload: dict[str, Any],
    session: SessionDep,
    resolver: ResolverDep,
    x_freshsales_webhook_secret: Annotated[str | None, Header()] = None,
) -> dict[str, str]:
    """Freshsales workflow automation webhook (spec §6A).

    Configured via Admin > Workflows > Automations > Trigger Webhook, pointed at
    `POST /webhooks/freshsales/deal` (no `/api/v1` prefix, per spec §5).
    """
    settings = get_settings()
    expected_secret = settings.freshsales_webhook_secret
    if expected_secret and x_freshsales_webhook_secret != expected_secret:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid webhook secret"
        )

    await ingest_webhook(session, payload, resolver)
    return {"status": "ok"}
