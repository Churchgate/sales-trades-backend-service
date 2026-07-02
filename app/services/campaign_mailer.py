# ruff: noqa: E501
"""Campaign-specific outbound email via SendGrid (WTC Abuja account).

Handles two templates:
  1. Digital-pack delivery — sent to the lead with their requested materials.
  2. Lead-capture notification — sent to the sales team when a new lead is captured.

Both use the WTC Abuja dark luxury brand identity (dark card, gold accents).
Uses a separate SendGrid account (enquiries@wtcabuja.com) from the shared
Churchgate no-reply account used by mailer.py.
"""

from datetime import UTC, datetime

import httpx

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.models.campaign import Campaign
from app.models.lead import Lead

logger = get_logger(__name__)

# ── shared dark brand palette ──────────────────────────────────────────────────

_GOLD = "#c79a3a"
_BG = "#0c0d0f"
_CARD = "#131418"
_INNER = "#0c0d0f"
_DIVIDER = "#1e2028"
_WHITE = "#ffffff"
_MUTED = "#d1d5db"
_DIM = "#4b5563"
_LABEL_GRAY = "#6b7280"


def _label(text: str) -> str:
    return (
        f'<div style="color:{_GOLD};font-size:9px;font-weight:700;'
        f'letter-spacing:0.14em;text-transform:uppercase;margin-bottom:6px">{text}</div>'
    )


def _divider() -> str:
    return f'<div style="height:1px;background:{_DIVIDER};margin:0 0 20px"></div>'


def _wtc_header(subtitle: str) -> str:
    return f"""\
      <table width="100%" cellpadding="0" cellspacing="0">
        <tr><td style="background:{_BG};padding:36px 40px 0;text-align:center">
          <div style="color:{_GOLD};font-size:10px;font-weight:700;letter-spacing:0.2em;text-transform:uppercase;margin-bottom:12px">WORLD TRADE CENTER</div>
          <div style="color:{_WHITE};font-size:30px;font-weight:800;margin-bottom:10px">WTC Abuja</div>
          <div style="color:{_GOLD};font-size:14px;margin-bottom:28px">{subtitle}</div>
          <div style="height:1px;background:linear-gradient(to right,transparent,{_GOLD} 20%,{_GOLD} 80%,transparent)"></div>
        </td></tr>
      </table>"""


def _wtc_footer(campaign_name: str, captured_at: str) -> str:
    return f"""\
      <table width="100%" cellpadding="0" cellspacing="0">
        <tr><td style="background:{_BG};border-top:1px solid {_DIVIDER};padding:22px 40px;text-align:center">
          <div style="color:{_DIM};font-size:12px;margin-bottom:4px">Captured via WTC Abuja Concierge App</div>
          <div style="color:{_DIM};font-size:12px;margin-bottom:14px">{captured_at} &middot; {campaign_name}</div>
          <div style="color:{_GOLD};font-size:9px;font-weight:700;letter-spacing:0.2em;text-transform:uppercase">WORLD TRADE CENTER ABUJA</div>
        </td></tr>
      </table>"""


def _wrap(body_html: str) -> str:
    """Wrap body content in the outer dark card shell."""
    return f"""\
<div style="background:{_BG};margin:0;padding:32px 16px;font-family:Arial,Helvetica,sans-serif">
  <table width="100%" cellpadding="0" cellspacing="0" style="max-width:600px;margin:0 auto">
    <tr><td style="background:{_CARD};border-radius:16px;overflow:hidden">
{body_html}
    </td></tr>
  </table>
</div>"""


# ── SMTP transport ─────────────────────────────────────────────────────────────


_SENDGRID_URL = "https://api.sendgrid.com/v3/mail/send"


async def send_campaign_email(
    to_email: str,
    subject: str,
    html: str,
    text: str,
    settings: Settings | None = None,
    from_email: str | None = None,
    from_name: str | None = None,
    cc: list[str] | None = None,
) -> bool:
    """Send one campaign email via SendGrid (WTC Abuja account). Returns True on success.

    No-ops when WTC_SENDGRID_API_KEY is unset — captures still work in dev/QA,
    they just skip the email. The override from_email/from_name falls back to
    event_mail_from_email/event_mail_from_name from settings. `cc` recipients get
    a copy (SendGrid rejects a cc that duplicates the to address, so those are
    dropped).
    """
    settings = settings or get_settings()

    if not settings.wtc_sendgrid_api_key:
        logger.warning(
            "wtc sendgrid api key not configured; skipping campaign email",
            to_email=to_email,
            subject=subject,
        )
        return False

    sender_email = from_email or settings.event_mail_from_email or settings.mail_from_email
    sender_name = from_name or settings.event_mail_from_name or settings.mail_from_name

    personalization: dict = {"to": [{"email": to_email}]}
    cc_recipients = [
        addr for addr in (cc or []) if addr and addr.lower() != to_email.lower()
    ]
    if cc_recipients:
        personalization["cc"] = [{"email": addr} for addr in cc_recipients]

    payload = {
        "personalizations": [personalization],
        "from": {"email": sender_email, "name": sender_name},
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
                headers={"Authorization": f"Bearer {settings.wtc_sendgrid_api_key}"},
            )
            response.raise_for_status()
    except httpx.HTTPError:
        logger.exception("campaign sendgrid send failed", to_email=to_email, subject=subject)
        return False

    logger.info("campaign email sent", to_email=to_email, subject=subject)
    return True


# ── Lead-capture notification (to sales team) ──────────────────────────────────


def build_lead_notification_email(lead: Lead, campaign: Campaign) -> tuple[str, str, str]:
    """(subject, html, text) for the internal new-lead notification."""
    full_name = " ".join(filter(None, [lead.first_name, lead.last_name])) or "Unknown"
    company = lead.company or "Not provided"
    job_title = lead.job_title or "Not provided"
    timing = lead.timing or "Not provided"
    captured_at = (
        lead.captured_at.astimezone(UTC).strftime("%-d %B %Y, %H:%M")
        if lead.captured_at
        else datetime.now(UTC).strftime("%-d %B %Y, %H:%M")
    )

    subject = f"New Lead: {full_name} — {campaign.name}"

    # ── plain text ─────────────────────────────────────────────────────────────
    materials_text = (
        "\n".join(f"  ✦ {m}" for m in lead.requested_materials)
        if lead.requested_materials
        else "  None"
    )
    text = (
        f"New Lead Notification — {campaign.name}\n"
        f"{'=' * 50}\n\n"
        f"Name:    {full_name}\n"
        f"Company: {company}\n"
        f"Title:   {job_title}\n"
        f"Timing:  {timing}\n"
        f"Email:   {lead.email}\n"
        + (f"Phone:   {lead.phone}\n" if lead.phone else "")
        + f"\nMaterials requested:\n{materials_text}\n\n"
        f"Inspection: {'YES' if lead.inspection_requested else 'NO'}\n"
        f"Marketing:  {'OPT-IN' if lead.marketing_opt_in else 'NO'}\n\n"
        f"Captured: {captured_at} via WTC Abuja Concierge App\n"
        f"Campaign: {campaign.name}\n"
    )

    # ── HTML ───────────────────────────────────────────────────────────────────
    phone_line = (
        f'<div style="color:{_GOLD};font-size:14px;margin-top:6px">'
        f'&#128241; {lead.phone}</div>'
        if lead.phone
        else ""
    )

    if lead.requested_materials:
        mat_items = "".join(
            f'<div style="color:{_MUTED};font-size:14px;margin-bottom:10px">'
            f'<span style="color:{_GOLD};margin-right:10px">&#10022;</span>{m}</div>'
            for m in lead.requested_materials
        )
    else:
        mat_items = f'<div style="color:{_LABEL_GRAY};font-size:14px">None requested</div>'

    materials_block = f"""\
          <div style="background:{_INNER};border-radius:10px;padding:20px 24px;margin:20px 0">
            {_label("MATERIALS")}
            {mat_items}
          </div>"""

    inspection_label = "YES" if lead.inspection_requested else "NO"
    if lead.marketing_opt_in:
        marketing_bg, marketing_color, marketing_label = _GOLD, "#15181e", "OPT-IN"
    else:
        marketing_bg, marketing_color, marketing_label = "#1a1c22", _LABEL_GRAY, "NO"

    body = f"""\
{_wtc_header("New Lead Notification")}
      <table width="100%" cellpadding="0" cellspacing="0">
        <tr><td style="padding:32px 40px">
          <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:20px">
            <tr>
              <td width="50%" style="vertical-align:top;padding-right:16px">
                {_label("NAME")}
                <div style="color:{_WHITE};font-size:16px;font-weight:700;line-height:1.35">{full_name}</div>
              </td>
              <td width="50%" style="vertical-align:top;padding-left:16px">
                {_label("COMPANY")}
                <div style="color:{_WHITE};font-size:16px;font-weight:700;line-height:1.35">{company}</div>
              </td>
            </tr>
          </table>
          {_divider()}
          <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:20px">
            <tr>
              <td width="50%" style="vertical-align:top;padding-right:16px">
                {_label("TITLE")}
                <div style="color:{_MUTED};font-size:14px">{job_title}</div>
              </td>
              <td width="50%" style="vertical-align:top;padding-left:16px">
                {_label("TIMING")}
                <div style="color:{_MUTED};font-size:14px">{timing}</div>
              </td>
            </tr>
          </table>
          {_divider()}
          {_label("CONTACT")}
          <div style="color:{_GOLD};font-size:14px"><a href="mailto:{lead.email}" style="color:{_GOLD};text-decoration:none">&#9993; {lead.email}</a></div>
          {phone_line}
          {materials_block}
          <table width="100%" cellpadding="0" cellspacing="0" style="margin-top:20px">
            <tr>
              <td width="50%" style="padding-right:6px">
                <div style="background:#1a1c22;border-radius:8px;padding:12px;text-align:center;color:{_LABEL_GRAY};font-size:11px;font-weight:700;letter-spacing:0.05em">&#128273; INSPECTION: {inspection_label}</div>
              </td>
              <td width="50%" style="padding-left:6px">
                <div style="background:{marketing_bg};border-radius:8px;padding:12px;text-align:center;color:{marketing_color};font-size:11px;font-weight:700;letter-spacing:0.05em">&#128226; MARKETING: {marketing_label}</div>
              </td>
            </tr>
          </table>
        </td></tr>
      </table>
{_wtc_footer(campaign.name, captured_at)}"""

    html = _wrap(body)
    return subject, html, text


async def send_lead_notification(
    lead: Lead,
    campaign: Campaign,
    settings: Settings | None = None,
) -> None:
    """Fire-and-forget internal notification to the sales team. Never raises."""
    settings = settings or get_settings()
    if not settings.campaign_notification_email:
        return

    try:
        subject, html, text = build_lead_notification_email(lead, campaign)
        await send_campaign_email(
            to_email=settings.campaign_notification_email,
            subject=subject,
            html=html,
            text=text,
            settings=settings,
        )
    except Exception:
        logger.exception("lead notification email failed", lead_id=lead.id)


# ── Digital-pack delivery email (to lead) ─────────────────────────────────────


def build_pack_email(
    lead: Lead, campaign: Campaign, materials: list[tuple[str, list[str]]]
) -> tuple[str, str, str]:
    """(subject, html, text) for the digital-pack email sent to the visitor.

    Overridable via config["digital_pack"] = {
        "subject": ..., "intro": ..., "logo_url": ...,
        "contact_email": ..., "contact_phone": ...,
    }
    """
    pack_cfg = (campaign.config or {}).get("digital_pack", {})
    event_name = campaign.name
    subject = pack_cfg.get("subject", f"Your {event_name} digital pack")
    intro = pack_cfg.get(
        "intro",
        "Thank you for visiting us. Your requested materials are ready to download.",
    )
    logo_url = pack_cfg.get("logo_url")
    contact_email = pack_cfg.get("contact_email")
    contact_phone = pack_cfg.get("contact_phone")
    greeting = f"Hello {lead.first_name}," if lead.first_name else "Hello,"
    captured_at = (
        lead.captured_at.astimezone(UTC).strftime("%-d %B %Y")
        if lead.captured_at
        else datetime.now(UTC).strftime("%-d %B %Y")
    )

    def _file_label(index: int, total: int) -> str:
        return f"Download {index + 1}" if total > 1 else "Download"

    # ── plain text ─────────────────────────────────────────────────────────────
    text_lines = [greeting, "", intro, ""]
    for label, urls in materials:
        text_lines.append(f"{label}:")
        for i, url in enumerate(urls):
            text_lines.append(f"  {_file_label(i, len(urls))}: {url}")
        text_lines.append("")
    if contact_email or contact_phone:
        text_lines.append("Questions? We're happy to help.")
        if contact_email:
            text_lines.append(f"Email: {contact_email}")
        if contact_phone:
            text_lines.append(f"Phone: {contact_phone}")
    text = "\n".join(text_lines).rstrip() + "\n"

    # ── HTML ───────────────────────────────────────────────────────────────────
    _btn = (
        f"display:inline-block;background:{_GOLD};color:#15181e;text-decoration:none;"
        "font-weight:700;font-size:13px;padding:10px 18px;border-radius:8px;"
        "margin:6px 8px 0 0;letter-spacing:0.02em"
    )

    logo_block = (
        f'<div style="text-align:center;margin-bottom:8px">'
        f'<img src="{logo_url}" alt="{event_name}" style="max-width:180px;height:auto"/>'
        f"</div>"
        if logo_url
        else ""
    )

    material_cards = "\n".join(
        f"""\
          <div style="background:#1a1c22;border-radius:10px;padding:18px 20px;margin-bottom:12px">
            <div style="color:{_WHITE};font-size:15px;font-weight:700;margin-bottom:10px">{label}</div>
            {"".join(
                f'<a href="{url}" style="{_btn}">{_file_label(i, len(urls))}</a>'
                for i, url in enumerate(urls)
            )}
          </div>"""
        for label, urls in materials
    )

    if contact_email or contact_phone:
        contact_parts = []
        if contact_email:
            contact_parts.append(
                f'<a href="mailto:{contact_email}" style="color:{_GOLD};text-decoration:none">'
                f"{contact_email}</a>"
            )
        if contact_phone:
            contact_parts.append(contact_phone)
        contact_block = (
            f'<div style="color:{_MUTED};font-size:13px;margin-top:24px">'
            f"Questions? We're happy to help &mdash; "
            + " &middot; ".join(contact_parts)
            + "</div>"
        )
    else:
        contact_block = ""

    body = f"""\
{_wtc_header(subject)}
      <table width="100%" cellpadding="0" cellspacing="0">
        <tr><td style="padding:32px 40px">
          {logo_block}
          <div style="color:{_MUTED};font-size:15px;margin-bottom:6px">{greeting}</div>
          <div style="color:{_MUTED};font-size:14px;margin-bottom:24px">{intro}</div>
          {_label("YOUR MATERIALS")}
{material_cards}
          {contact_block}
          <div style="color:{_DIM};font-size:12px;margin-top:24px">
            If a download link doesn't open, reply to this email and we'll help.
          </div>
        </td></tr>
      </table>
{_wtc_footer(campaign.name, captured_at)}"""

    html = _wrap(body)
    return subject, html, text
