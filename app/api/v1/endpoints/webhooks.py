import json
from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException, Request, status

from app.api.dependencies import ResolverDep, SessionDep
from app.core.config import get_settings
from app.core.logging import get_logger
from app.services import email_event_ingest
from app.services.webhook_ingest import ingest_webhook

logger = get_logger(__name__)

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


@router.post("/sendgrid/events", status_code=status.HTTP_200_OK)
async def sendgrid_events_webhook(
    request: Request,
    session: SessionDep,
    x_twilio_email_event_webhook_signature: Annotated[str | None, Header()] = None,
    x_twilio_email_event_webhook_timestamp: Annotated[str | None, Header()] = None,
) -> dict[str, str]:
    """SendGrid Event Webhook — email open/click tracking for campaign packs.

    Configured via SendGrid > Settings > Mail Settings > Event Webhook, pointed at
    this URL (no `/api/v1` prefix, matching the Freshsales webhook above). Enable
    "Signed Event Webhook" there and set SENDGRID_WEBHOOK_PUBLIC_KEY to the key it
    shows — without it this endpoint accepts unsigned requests (logged, not blocked)
    since the key isn't available until that's turned on in SendGrid's dashboard.
    """
    raw_body = await request.body()
    settings = get_settings()

    if settings.sendgrid_webhook_public_key:
        valid = (
            x_twilio_email_event_webhook_signature is not None
            and x_twilio_email_event_webhook_timestamp is not None
            and email_event_ingest.verify_signature(
                raw_body,
                x_twilio_email_event_webhook_signature,
                x_twilio_email_event_webhook_timestamp,
                settings.sendgrid_webhook_public_key,
            )
        )
        if not valid:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid webhook signature"
            )
    else:
        logger.warning("sendgrid webhook received without signature verification configured")

    events = json.loads(raw_body)
    processed = await email_event_ingest.ingest_events(session, events)
    return {"status": "ok", "processed": str(processed)}
