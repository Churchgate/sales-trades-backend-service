"""Ingest SendGrid Event Webhook POSTs (open/click/delivered/bounce/...) for
campaign emails, correlating each event back to the Lead it was sent to.

Correlation uses `custom_args.lead_id`, set on every campaign send (see
campaign_mailer.send_campaign_email) — SendGrid echoes custom_args back as
top-level fields on each event. Emails sent before this existed have no lead_id
and their events are silently dropped (nothing to attribute them to).

`click` events additionally carry the exact original URL clicked; we resolve that
against every campaign's `materials_assets` to know WHICH document (not just "some
link") was opened, and record it in `Lead.pack_clicked_materials` + the full
per-event history in `email_events`.
"""

import base64
from datetime import UTC, datetime
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.serialization import load_der_public_key
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.email_event import (
    EVENT_CLICK,
    EVENT_OPEN,
    EmailEvent,
)
from app.repositories import campaigns_repo, email_events_repo, leads_repo

logger = get_logger(__name__)

# Event types we persist at all (bounce/dropped/spamreport/unsubscribe kept for
# deliverability visibility even though only open/click feed the Lead rollups).
_TRACKED_EVENTS = {
    EVENT_OPEN,
    EVENT_CLICK,
    "delivered",
    "bounce",
    "dropped",
    "spamreport",
    "unsubscribe",
}


def verify_signature(payload: bytes, signature: str, timestamp: str, public_key_b64: str) -> bool:
    """Verify a SendGrid Signed Event Webhook request (ECDSA P-256 / SHA-256).

    `signature`/`timestamp` are the X-Twilio-Email-Event-Webhook-Signature /
    -Timestamp headers; `payload` is the exact raw request body bytes (signing
    covers timestamp + raw body, so a parsed-then-reserialized body won't verify).
    """
    try:
        public_key = load_der_public_key(
            base64.b64decode(public_key_b64), backend=default_backend()
        )
        decoded_signature = base64.b64decode(signature)
        public_key.verify(decoded_signature, timestamp.encode() + payload, ec.ECDSA(SHA256()))
        return True
    except (InvalidSignature, ValueError):
        return False


async def _resolve_material(session: AsyncSession, url: str | None) -> str | None:
    """Match a clicked URL against every campaign's materials_assets -> label."""
    if not url:
        return None
    campaigns = await campaigns_repo.list_all(session)
    for campaign in campaigns:
        assets: dict[str, Any] = (campaign.config or {}).get("materials_assets", {})
        for label, value in assets.items():
            urls = value if isinstance(value, list) else [value]
            if url in urls:
                return label
    return None


async def ingest_events(session: AsyncSession, events: list[dict[str, Any]]) -> int:
    """Process a batch of SendGrid webhook events. Never raises — a malformed or
    unattributable event is logged and skipped so one bad row can't drop the batch."""
    processed = 0
    for evt in events:
        try:
            if await _ingest_one(session, evt):
                processed += 1
        except Exception:  # noqa: BLE001 — one bad event must not sink the batch
            logger.exception("email event ingest failed", event=evt.get("event"))
    return processed


async def _ingest_one(session: AsyncSession, evt: dict[str, Any]) -> bool:
    event_type = evt.get("event")
    if event_type not in _TRACKED_EVENTS:
        return False

    sg_event_id = evt.get("sg_event_id")
    if not sg_event_id:
        return False
    if await email_events_repo.get_by_sg_event_id(session, sg_event_id):
        return False  # already recorded — SendGrid is at-least-once delivery

    lead_id_raw = evt.get("lead_id")
    if not lead_id_raw or not str(lead_id_raw).isdigit():
        return False  # no custom_args -> can't attribute (e.g. pre-tracking sends)
    lead_id = int(lead_id_raw)

    url = evt.get("url") if event_type == EVENT_CLICK else None
    material = await _resolve_material(session, url) if url else None
    occurred_at = (
        datetime.fromtimestamp(evt["timestamp"], tz=UTC)
        if evt.get("timestamp")
        else datetime.now(UTC)
    )

    await email_events_repo.create(
        session,
        EmailEvent(
            lead_id=lead_id,
            email_kind=evt.get("email_kind"),
            event_type=event_type,
            material=material,
            url=url,
            sg_event_id=sg_event_id,
            sg_message_id=evt.get("sg_message_id"),
            occurred_at=occurred_at,
        ),
    )

    if event_type in (EVENT_OPEN, EVENT_CLICK):
        await _bump_lead_rollup(session, lead_id, event_type, material, occurred_at)
    return True


async def _bump_lead_rollup(
    session: AsyncSession,
    lead_id: int,
    event_type: str,
    material: str | None,
    occurred_at: datetime,
) -> None:
    lead = await leads_repo.get(session, lead_id)
    if lead is None:
        return
    changed = False
    if event_type == EVENT_OPEN:
        if lead.pack_opened_at is None:
            lead.pack_opened_at = occurred_at
            changed = True
        lead.pack_opened_count = (lead.pack_opened_count or 0) + 1
        changed = True
    elif event_type == EVENT_CLICK and material:
        clicked = list(lead.pack_clicked_materials or [])
        if material not in clicked:
            clicked.append(material)
            lead.pack_clicked_materials = clicked
            changed = True
    if changed:
        await leads_repo.update(session, lead)
