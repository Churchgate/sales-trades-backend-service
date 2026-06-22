"""Outbound transactional email via SendGrid (v3 Mail Send API).

Kept deliberately thin: a single async send over httpx, mirroring the Freshsales
client's style. Callers treat email as best-effort — a send failure is logged and
swallowed so it never rolls back a confirmed booking.
"""

import httpx

from app.core.config import Settings, get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_SENDGRID_URL = "https://api.sendgrid.com/v3/mail/send"


async def send_email(
    to_email: str,
    subject: str,
    html: str,
    text: str,
    settings: Settings | None = None,
) -> bool:
    """Send one email. Returns True on success (HTTP 2xx), False otherwise.

    No-ops (returns False) when SENDGRID_API_KEY is unset — handy for local dev so
    bookings still work without a live SendGrid account.
    """
    settings = settings or get_settings()

    if not settings.sendgrid_api_key:
        logger.warning(
            "sendgrid api key not configured; skipping email", to_email=to_email, subject=subject
        )
        return False

    payload = {
        "personalizations": [{"to": [{"email": to_email}]}],
        "from": {"email": settings.mail_from_email, "name": settings.mail_from_name},
        "subject": subject,
        "content": [
            {"type": "text/plain", "value": text},
            {"type": "text/html", "value": html},
        ],
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                _SENDGRID_URL,
                json=payload,
                headers={"Authorization": f"Bearer {settings.sendgrid_api_key}"},
            )
            response.raise_for_status()
    except httpx.HTTPError:
        logger.exception("sendgrid send failed", to_email=to_email, subject=subject)
        return False

    logger.info("email sent", to_email=to_email, subject=subject)
    return True
