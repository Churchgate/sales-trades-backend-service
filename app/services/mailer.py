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
    from_email: str | None = None,
    from_name: str | None = None,
) -> bool:
    """Send one email. Returns True on success (HTTP 2xx), False otherwise.

    No-ops (returns False) when SENDGRID_API_KEY is unset — handy for local dev so
    bookings still work without a live SendGrid account.

    `from_email`/`from_name` default to `settings.mail_from_email`/`mail_from_name`
    (the shared no-reply sender) — pass them explicitly when a feature needs its
    own sender identity (e.g. event emails from `events@wtcabuja.com` instead of
    bookings' `no-reply@churchgate.com`). The override address must be a verified
    Sender Identity (or part of an authenticated domain) in SendGrid, or sends
    will fail.
    """
    settings = settings or get_settings()

    if not settings.sendgrid_api_key:
        logger.warning(
            "sendgrid api key not configured; skipping email", to_email=to_email, subject=subject
        )
        return False

    payload = {
        "personalizations": [{"to": [{"email": to_email}]}],
        "from": {
            "email": from_email or settings.mail_from_email,
            "name": from_name or settings.mail_from_name,
        },
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


async def send_invite_email(
    to_email: str,
    temp_password: str,
    settings: Settings | None = None,
) -> bool:
    """Invite a new dashboard user: link to the login page + their temporary
    password, with a nudge to change it on first sign-in. Best-effort like all
    sends — returns False (and the caller surfaces the password) if SendGrid is
    unconfigured."""
    settings = settings or get_settings()
    login_url = f"{settings.frontend_base_url.rstrip('/')}/admin/login"

    subject = "Your RB Properties sales dashboard access"
    text = (
        "You've been invited to the RB Properties sales dashboard.\n\n"
        f"Sign in at: {login_url}\n"
        f"Email:    {to_email}\n"
        f"Temporary password: {temp_password}\n\n"
        "You'll be asked to set your own password the first time you sign in.\n"
    )
    html = f"""\
<div style="font-family:Arial,Helvetica,sans-serif;max-width:560px;margin:auto;color:#15181e">
  <h2 style="margin-bottom:4px">Welcome to the sales dashboard</h2>
  <p>You've been invited to the RB Properties sales intelligence dashboard.</p>
  <div style="background:#f4f6f8;border-radius:8px;padding:16px;margin:16px 0">
    <table style="border-collapse:collapse;width:100%">
      <tr><td style="padding:6px 0;color:#666">Email</td>
          <td style="padding:6px 0;font-weight:600">{to_email}</td></tr>
      <tr><td style="padding:6px 0;color:#666">Temporary password</td>
          <td style="padding:6px 0;font-weight:600;letter-spacing:.04em">{temp_password}</td></tr>
    </table>
  </div>
  <p style="margin:16px 0">
    <a href="{login_url}"
       style="display:inline-block;background:#c79a3a;color:#15181e;text-decoration:none;
              font-weight:600;padding:12px 22px;border-radius:10px">Sign in</a>
  </p>
  <p style="color:#666;font-size:13px">
    You'll be asked to set your own password the first time you sign in.
  </p>
</div>
"""
    return await send_email(
        to_email=to_email, subject=subject, html=html, text=text, settings=settings
    )
