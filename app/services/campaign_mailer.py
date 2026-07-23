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
    lead_id: int | None = None,
    email_kind: str | None = None,
) -> bool:
    """Send one campaign email via SendGrid (WTC Abuja account). Returns True on success.

    No-ops when WTC_SENDGRID_API_KEY is unset — captures still work in dev/QA,
    they just skip the email. The override from_email/from_name falls back to
    event_mail_from_email/event_mail_from_name from settings. `cc` recipients get
    a copy (SendGrid rejects a cc that duplicates the to address, so those are
    dropped).

    `lead_id`/`email_kind` (e.g. "pack"/"viewing") are set as SendGrid custom_args
    so the Event Webhook (open/click tracking, services/email_event_ingest.py) can
    attribute engagement back to this exact lead/send — omit for emails not sent to
    a lead's own inbox (e.g. the internal staff notification).
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

    payload: dict = {
        "personalizations": [personalization],
        "from": {"email": sender_email, "name": sender_name},
        "subject": subject,
        "content": [
            {"type": "text/plain", "value": text},
            {"type": "text/html", "value": html},
        ],
        "tracking_settings": {
            "click_tracking": {"enable": True, "enable_text": False},
            "open_tracking": {"enable": True},
        },
    }
    if lead_id is not None:
        custom_args = {"lead_id": str(lead_id)}
        if email_kind:
            custom_args["email_kind"] = email_kind
        payload["custom_args"] = custom_args

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


# ══ Visitor emails — pack delivery + viewing confirmation ═════════════════════
# Rebuilt from the WTC-Abuja-Email-Templates designs as bulletproof, table-only
# HTML (no position/flex/object-fit/filter/gradients) so Gmail and Outlook render
# them. Light "cream" brand: dark header, white body, gold accents, serif heads.

_C_CREAM = "#f0ede6"
_C_INK = "#111110"
_C_INK_SOFT = "#444442"
_C_MUTED = "#888885"
_C_GOLD = "#c9a84c"
_C_GOLD_DK = "#b8960c"
_C_LINE = "#e0ddd6"
_C_PANEL = "#f4f1eb"
_C_STRIP = "#1a1a18"
_C_FOOT = "#0d0d0c"
_C_HERO_BG = "#111110"
_SERIF = "Georgia,'Times New Roman',serif"
_SANS = "'Helvetica Neue',Helvetica,Arial,sans-serif"

# Email <head>: charset + a mobile media query. On phones (<=480px) each download
# row stacks — the button drops full-width below its text instead of being squeezed
# into a collapsed right column (which made the label wrap letter-by-letter). Desktop
# and Outlook ignore the media query and keep the two-column look; the button's
# white-space:nowrap is the fallback for clients that strip <style>.
_HEAD = (
    '<!DOCTYPE html>\n<html><head><meta charset="UTF-8">'
    '<meta name="viewport" content="width=device-width,initial-scale=1">'
    "<style>"
    "@media only screen and (max-width:480px){"
    ".wtc-row-text{display:block!important;width:100%!important;}"
    ".wtc-row-btn{display:block!important;width:100%!important;"
    "padding-left:0!important;text-align:left!important;}"
    ".wtc-btn{margin:14px 0 0 0!important;width:100%!important;}"
    ".wtc-btn a{display:block!important;text-align:center!important;}"
    "}"
    "</style></head>"
)


def _c_button(url: str, label: str, *, gold: bool = False) -> str:
    """Bulletproof (Outlook-safe) download button: bgcolor on a table cell."""
    bg = _C_GOLD_DK if gold else _C_INK
    return (
        f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
        f'class="wtc-btn" style="display:inline-block;margin:6px 0 0 8px;">'
        f'<tr><td bgcolor="{bg}" style="border-radius:2px;">'
        f'<a href="{url}" target="_blank" style="display:inline-block;padding:11px 22px;'
        f'font-family:{_SANS};font-size:10px;font-weight:700;letter-spacing:0.1em;'
        f'white-space:nowrap;text-transform:uppercase;color:#ffffff;'
        f'text-decoration:none;">{label} &#8595;</a>'
        f"</td></tr></table>"
    )


def _c_header(tag: str, heading_html: str, hero_url: str, logo_url: str) -> str:
    """Dark hero with the logo + heading overlaid on the (pre-darkened) photo.

    Bulletproof background: the `background` attribute carries the image for Gmail/
    Apple Mail while an mso-only VML `<v:rect>` fills it for Outlook; `bgcolor`
    is the solid-dark fallback if a client drops both. The photo must already be
    dark enough for white text (we darken it before hosting) since email clients
    strip CSS gradient scrims. Content flows normally (no position/flex).
    """
    if logo_url:
        brand = (
            f'<img src="{logo_url}" width="160" alt="World Trade Center Abuja" '
            f'style="width:160px;height:auto;display:block;border:0;">'
        )
    else:
        brand = (
            f'<span style="font-family:{_SANS};font-size:11px;font-weight:700;'
            f'letter-spacing:0.22em;text-transform:uppercase;color:#ffffff;">'
            f"World Trade Center <span style=\"color:{_C_GOLD};\">Abuja</span></span>"
        )
    inner = f"""\
            <table width="100%" cellpadding="0" cellspacing="0" border="0" role="presentation">
              <tr>
                <td style="padding:30px 40px 34px;">
                  <table width="100%" cellpadding="0" cellspacing="0" border="0" role="presentation">
                    <tr>
                      <td align="left" style="vertical-align:middle;">{brand}</td>
                      <td align="right" style="vertical-align:middle;">
                        <span style="font-family:{_SANS};font-size:9px;font-weight:700;letter-spacing:0.2em;text-transform:uppercase;color:rgba(255,255,255,0.55);">{tag}</span>
                      </td>
                    </tr>
                  </table>
                  <div style="font-size:0;line-height:0;height:88px;">&nbsp;</div>
                  <p style="font-family:{_SANS};font-size:8px;font-weight:700;letter-spacing:0.2em;text-transform:uppercase;color:{_C_GOLD};margin:0 0 8px;">World Trade Center &middot; Abuja</p>
                  <p style="font-family:{_SERIF};font-size:27px;font-weight:400;color:#ffffff;line-height:1.15;margin:0;">{heading_html}</p>
                </td>
              </tr>
            </table>"""
    if not hero_url:
        return (
            f'        <tr>\n          <td bgcolor="{_C_HERO_BG}" '
            f'style="background:{_C_HERO_BG};border-radius:4px 4px 0 0;">\n'
            f"{inner}\n          </td>\n        </tr>"
        )
    return f"""\
        <tr>
          <td background="{hero_url}" bgcolor="{_C_HERO_BG}" valign="bottom" style="background-color:{_C_HERO_BG};background-image:url('{hero_url}');background-position:center;background-size:cover;border-radius:4px 4px 0 0;">
            <!--[if gte mso 9]>
            <v:rect xmlns:v="urn:schemas-microsoft-com:vml" fill="true" stroke="false" style="width:600px;height:300px;">
            <v:fill type="frame" src="{hero_url}" color="{_C_HERO_BG}" />
            <v:textbox inset="0,0,0,0">
            <![endif]-->
{inner}
            <!--[if gte mso 9]>
            </v:textbox>
            </v:rect>
            <![endif]-->
          </td>
        </tr>"""


def _c_footer(
    contact_lead_in: str,
    footer_note_html: str,
    footer_email: str = "enquiries@wtcabuja.com",
) -> str:
    return f"""\
        <tr>
          <td style="background:{_C_STRIP};padding:24px 48px;">
            <table width="100%" cellpadding="0" cellspacing="0" border="0" role="presentation">
              <tr>
                <td style="vertical-align:middle;">
                  <p style="font-family:{_SANS};font-size:11px;color:#8f8f8b;margin:0 0 4px;">{contact_lead_in}</p>
                  <a href="mailto:{footer_email}" style="font-family:{_SANS};font-size:12px;color:{_C_GOLD};font-weight:600;text-decoration:none;">{footer_email}</a>
                </td>
                <td align="right" style="vertical-align:middle;">
                  <a href="https://wtcabuja.com" style="font-family:{_SANS};font-size:8px;letter-spacing:0.1em;text-transform:uppercase;color:{_C_GOLD};text-decoration:none;">wtcabuja.com</a>
                </td>
              </tr>
            </table>
          </td>
        </tr>
        <tr>
          <td style="background:{_C_FOOT};padding:20px 48px;border-radius:0 0 4px 4px;">
            <p style="font-family:{_SANS};font-size:9px;color:#4a4a46;line-height:1.7;margin:0;">{footer_note_html}</p>
          </td>
        </tr>"""


def _c_shell(*, tag: str, heading_html: str, body_html: str, contact_lead_in: str,
             footer_note_html: str, hero_url: str, logo_url: str,
             footer_email: str = "enquiries@wtcabuja.com") -> str:
    """Wrap the header + body + footer in the cream outer shell."""
    return f"""\
<body style="margin:0;padding:0;background:{_C_CREAM};">
  <table width="100%" cellpadding="0" cellspacing="0" border="0" role="presentation" style="background:{_C_CREAM};">
    <tr>
      <td align="center" style="padding:40px 16px;">
        <table width="600" cellpadding="0" cellspacing="0" border="0" role="presentation" style="max-width:600px;width:100%;">
{_c_header(tag, heading_html, hero_url, logo_url)}
          <tr>
            <td style="background:#ffffff;padding:40px 48px 36px;">
{body_html}
            </td>
          </tr>
{_c_footer(contact_lead_in, footer_note_html, footer_email)}
        </table>
      </td>
    </tr>
  </table>
</body>"""


def _greeting(lead: Lead) -> str:
    return f"Hello {lead.first_name}," if lead.first_name else "Hello,"


def _btn_label(i: int, total: int) -> str:
    return f"Download {i + 1}" if total > 1 else "Download"


def all_materials(config: dict) -> list[tuple[str, list[str]]]:
    """Every configured material with a download asset, in ``materials`` order.

    The full document set for the campaign — used by the viewing email, which has
    no per-lead material selection, so viewing registrants still receive the same
    brochure + floorplans as the digital pack.
    """
    assets: dict = config.get("materials_assets", {})
    out: list[tuple[str, list[str]]] = []
    for label in config.get("materials", []):
        entry = assets.get(label)
        if not entry:
            continue
        urls = [entry] if isinstance(entry, str) else [u for u in entry if u]
        if urls:
            out.append((label, urls))
    return out


def _material_rows(materials: list[tuple[str, list[str]]], display: dict) -> str:
    """Shared download-row markup for the pack + viewing emails (one row per
    material; ``featured`` renders the highlighted cream panel)."""
    rows = []
    for label, urls in materials:
        meta = display.get(label, {})
        eyebrow = meta.get("eyebrow", "Document")
        title = meta.get("title", label)
        description = meta.get("description", "")
        featured = bool(meta.get("featured"))
        buttons = "".join(
            _c_button(u, _btn_label(i, len(urls)), gold=featured)
            for i, u in enumerate(urls)
        )
        desc_html = (
            f'<p style="font-family:{_SANS};font-size:11px;color:{_C_MUTED};'
            f'margin:4px 0 0;line-height:1.5;">{description}</p>'
            if description
            else ""
        )
        inner = f"""\
                    <table width="100%" cellpadding="0" cellspacing="0" border="0" role="presentation">
                      <tr>
                        <td class="wtc-row-text" style="vertical-align:middle;">
                          <p style="font-family:{_SANS};font-size:8px;font-weight:700;letter-spacing:0.18em;text-transform:uppercase;color:{_C_GOLD_DK};margin:0 0 4px;">{eyebrow}</p>
                          <p style="font-family:{_SERIF};font-size:16px;color:{_C_INK};margin:0;">{title}</p>
                          {desc_html}
                        </td>
                        <td class="wtc-row-btn" align="right" style="vertical-align:middle;padding-left:16px;">{buttons}</td>
                      </tr>
                    </table>"""
        if featured:
            rows.append(
                f'<tr><td style="border-top:1px solid {_C_LINE};padding-top:22px;">'
                f'<table width="100%" cellpadding="0" cellspacing="0" border="0" role="presentation">'
                f'<tr><td style="background:{_C_PANEL};border-radius:3px;padding:20px 22px;">'
                f"{inner}</td></tr></table></td></tr>"
            )
        else:
            rows.append(
                f'<tr><td style="border-top:1px solid {_C_LINE};padding:22px 0 20px;">'
                f"{inner}</td></tr>"
            )
    return "\n".join(rows)


# ── Digital-pack delivery email (to lead) ─────────────────────────────────────


def build_pack_email(
    lead: Lead, campaign: Campaign, materials: list[tuple[str, list[str]]]
) -> tuple[str, str, str]:
    """(subject, html, text) for the digital-pack email sent to the visitor.

    Dynamic: one download row per requested material that has a configured asset
    (`materials`, from pack_delivery.deliverable_materials). Copy is source-aware
    via config["digital_pack"]:
        subject, intro, event_line (footer; NOG only), hero_url, logo_url
    and each row's label/blurb via config["materials_display"][label] =
        {eyebrow, title, description, featured}.
    """
    config = campaign.config or {}
    pack_cfg = config.get("digital_pack", {})
    display: dict = config.get("materials_display", {})
    subject = pack_cfg.get("subject", "Your WTC Abuja Digital Pack")
    intro = pack_cfg.get(
        "intro",
        "Thank you for your interest in World Trade Center Abuja. As requested, "
        "your materials are below — tap any button to download.",
    )
    event_line = pack_cfg.get("event_line", "")
    hero_url = pack_cfg.get("hero_url", "")
    logo_url = pack_cfg.get("logo_url", "")
    greeting = _greeting(lead)

    # ── plain text ───────────────────────────────────────────────────────────
    text_lines = [greeting, "", intro, ""]
    for label, urls in materials:
        meta = display.get(label, {})
        text_lines.append(meta.get("title", label) + ":")
        for i, url in enumerate(urls):
            text_lines.append(f"  {_btn_label(i, len(urls))}: {url}")
        text_lines.append("")
    text_lines.append("Questions? enquiries@wtcabuja.com")
    text = "\n".join(text_lines).rstrip() + "\n"

    # ── HTML rows ────────────────────────────────────────────────────────────
    rows_html = _material_rows(materials, display)

    body_html = f"""\
              <p style="font-family:{_SANS};font-size:15px;color:{_C_INK};line-height:1.6;margin:0 0 8px;">{greeting}</p>
              <p style="font-family:{_SANS};font-size:14px;color:{_C_INK_SOFT};line-height:1.7;margin:0 0 32px;">{intro}</p>
              <table width="100%" cellpadding="0" cellspacing="0" border="0" role="presentation">
{rows_html}
              </table>"""

    footer_bits = []
    if event_line:
        footer_bits.append(f"Captured via WTC Abuja Concierge App &nbsp;&middot;&nbsp; {event_line}")
    footer_bits.append("World Trade Center Abuja &nbsp;&middot;&nbsp; Central Business District, Abuja, Nigeria")
    footer_bits.append("If a download link doesn't open, reply to this email and we'll assist.")
    footer_note = "<br>".join(footer_bits)

    html = _HEAD + _c_shell(
        tag="Digital Pack",
        heading_html="Your materials<br>are ready.",
        body_html=body_html,
        contact_lead_in="Questions? We're here.",
        footer_note_html=footer_note,
        hero_url=hero_url,
        logo_url=logo_url,
    ) + "</html>"
    return subject, html, text


# ── Viewing-request confirmation email (to lead) ──────────────────────────────


_VIEWING_STEPS = [
    ("Confirmation call",
     "Our team will contact you to confirm a convenient date and time for your private viewing."),
    ("Private tour",
     "A dedicated host will walk you through the offices, residences, and clubhouse — tailored to your interests."),
    ("Tailored proposal",
     "Following your visit, we'll prepare a proposal specific to your requirements — whether office, residence, or both."),
]


def build_viewing_booking_email(lead: Lead, campaign: Campaign) -> tuple[str, str, str]:
    """(subject, html, text) confirming a viewing/inspection request to the visitor.

    Copy via config["viewing_booking"] = {subject, intro, brochure_url} and the
    shared config["digital_pack"] hero_url/logo_url. Viewing registrants don't pick
    materials, so the "Before your visit" section carries the full document set
    (config["materials"]/["materials_assets"]) — brochure + floorplans — falling
    back to the single brochure_url CTA when no materials are configured.
    """
    config = campaign.config or {}
    view_cfg = config.get("viewing_booking", {})
    pack_cfg = config.get("digital_pack", {})
    display: dict = config.get("materials_display", {})
    materials = all_materials(config)
    subject = view_cfg.get("subject", "Your WTC Abuja Viewing Request")
    intro = view_cfg.get(
        "intro",
        "Thank you for requesting a viewing at World Trade Center Abuja. A member "
        "of our team will be in touch with you shortly to confirm the details and "
        "arrange a time that works for you.",
    )
    brochure_url = view_cfg.get("brochure_url", "")
    hero_url = pack_cfg.get("hero_url", "")
    logo_url = pack_cfg.get("logo_url", "")
    greeting = _greeting(lead)

    # ── plain text ───────────────────────────────────────────────────────────
    text_lines = [greeting, "", intro, "", "Your viewing experience:"]
    for i, (title, desc) in enumerate(_VIEWING_STEPS, 1):
        text_lines.append(f"  {i}. {title} — {desc}")
    if materials:
        text_lines += ["", "Before your visit — your documents:"]
        for label, urls in materials:
            meta = display.get(label, {})
            text_lines.append(meta.get("title", label) + ":")
            for i, url in enumerate(urls):
                text_lines.append(f"  {_btn_label(i, len(urls))}: {url}")
    elif brochure_url:
        text_lines += ["", f"Download the full brochure: {brochure_url}"]
    text_lines += ["", "Questions? enquiries@wtcabuja.com"]
    text = "\n".join(text_lines) + "\n"

    # ── HTML steps ───────────────────────────────────────────────────────────
    steps = []
    for i, (title, desc) in enumerate(_VIEWING_STEPS, 1):
        steps.append(f"""\
                  <tr>
                    <td style="vertical-align:top;width:34px;padding:0 0 16px;">
                      <table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>
                        <td width="22" height="22" align="center" bgcolor="{_C_INK}" style="border-radius:11px;font-family:{_SERIF};font-size:11px;color:{_C_GOLD};line-height:22px;">{i}</td>
                      </tr></table>
                    </td>
                    <td style="vertical-align:top;padding:0 0 16px 4px;">
                      <p style="font-family:{_SANS};font-size:12px;font-weight:700;color:{_C_INK};margin:0 0 3px;">{title}</p>
                      <p style="font-family:{_SANS};font-size:12px;color:{_C_MUTED};line-height:1.5;margin:0;">{desc}</p>
                    </td>
                  </tr>""")
    steps_html = "\n".join(steps)

    # Viewing registrants receive the full document set (brochure + floorplans),
    # not just a brochure teaser. Fall back to the single brochure CTA only if no
    # materials are configured.
    docs_rows = _material_rows(materials, display)
    if docs_rows:
        docs_block = f"""\
              <p style="font-family:{_SANS};font-size:8px;font-weight:700;letter-spacing:0.18em;text-transform:uppercase;color:{_C_GOLD_DK};margin:0 0 4px;">Before your visit</p>
              <p style="font-family:{_SANS};font-size:13px;color:{_C_INK_SOFT};line-height:1.6;margin:0 0 4px;">Your documents are ready — tap any button to download.</p>
              <table width="100%" cellpadding="0" cellspacing="0" border="0" role="presentation">
{docs_rows}
              </table>"""
    elif brochure_url:
        docs_block = f"""\
              <table width="100%" cellpadding="0" cellspacing="0" border="0" role="presentation" style="margin-top:8px;">
                <tr>
                  <td style="background:{_C_PANEL};border-radius:3px;padding:22px 24px;">
                    <table width="100%" cellpadding="0" cellspacing="0" border="0" role="presentation">
                      <tr>
                        <td class="wtc-row-text" style="vertical-align:middle;">
                          <p style="font-family:{_SANS};font-size:8px;font-weight:700;letter-spacing:0.14em;text-transform:uppercase;color:{_C_GOLD_DK};margin:0 0 5px;">Prepare for your visit</p>
                          <p style="font-family:{_SERIF};font-size:15px;color:{_C_INK};margin:0 0 4px;">Download the full brochure</p>
                          <p style="font-family:{_SANS};font-size:11px;color:{_C_MUTED};line-height:1.5;margin:0;">Offices, residences, clubhouse and infrastructure — everything in one document.</p>
                        </td>
                        <td class="wtc-row-btn" align="right" style="vertical-align:middle;padding-left:16px;">{_c_button(brochure_url, "Download")}</td>
                      </tr>
                    </table>
                  </td>
                </tr>
              </table>"""
    else:
        docs_block = ""

    body_html = f"""\
              <p style="font-family:{_SANS};font-size:15px;color:{_C_INK};line-height:1.6;margin:0 0 8px;">{greeting}</p>
              <p style="font-family:{_SANS};font-size:14px;color:{_C_INK_SOFT};line-height:1.7;margin:0 0 32px;">{intro}</p>
              <p style="font-family:{_SANS};font-size:8px;font-weight:700;letter-spacing:0.18em;text-transform:uppercase;color:{_C_GOLD_DK};margin:0 0 18px;padding-top:22px;border-top:1px solid {_C_LINE};">Your viewing experience</p>
              <table width="100%" cellpadding="0" cellspacing="0" border="0" role="presentation" style="margin-bottom:24px;">
{steps_html}
              </table>
{docs_block}"""

    _domain = (
        f'<a href="https://wtcabuja.com" style="color:{_C_GOLD};text-decoration:none;">'
        f"wtcabuja.com</a>"
    )
    footer_note = (
        f"You are receiving this because you requested a viewing at {_domain}.<br>"
        "World Trade Center Abuja &nbsp;&middot;&nbsp; Central Business District, Abuja, Nigeria"
    )
    html = _HEAD + _c_shell(
        tag="Viewing Request",
        heading_html="Your viewing request<br>has been received.",
        body_html=body_html,
        contact_lead_in="Can't wait? Reach us directly.",
        footer_note_html=footer_note,
        hero_url=hero_url,
        logo_url=logo_url,
    ) + "</html>"
    return subject, html, text


async def send_viewing_booking(
    lead: Lead, campaign: Campaign, settings: Settings | None = None
) -> bool:
    """Best-effort viewing-confirmation email to the visitor. Never raises."""
    settings = settings or get_settings()
    try:
        subject, html, text = build_viewing_booking_email(lead, campaign)
        return await send_campaign_email(
            to_email=lead.email,
            subject=subject,
            html=html,
            text=text,
            settings=settings,
            cc=[settings.campaign_cc_email] if settings.campaign_cc_email else None,
            lead_id=lead.id,
            email_kind="viewing",
        )
    except Exception:
        logger.exception("viewing booking email failed", lead_id=lead.id)
        return False


# ── Application confirmation (to lead) ──────────────────────────────────────
# Sent once, on first capture, for campaigns that opt in via
# config["application_confirmation"] — e.g. export-launchpad-2026, whose
# applications have no materials/pack to deliver, so this is their only
# lead-facing email. Distinct from build_lead_notification_email (staff-only).


def build_application_confirmation_email(
    lead: Lead, campaign: Campaign, greeting_name: str | None = None
) -> tuple[str, str, str]:
    """(subject, html, text) confirming a received application to the applicant.

    Copy lives in config["application_confirmation"]: subject, eligibility
    criteria list, contact email/phone, response_days. Falls back to Export
    Launchpad's own defaults so a misconfigured campaign still sends something
    sane rather than erroring. ``greeting_name`` overrides the lead's own name
    in the salutation — used when this same email is also sent to the second
    participant.
    """
    config = campaign.config or {}
    cfg = config.get("application_confirmation", {})
    subject = cfg.get("subject", "Your WTC Abuja Application Has Been Received")
    programme_name = cfg.get("programme_name", "the programme")
    eligibility = cfg.get("eligibility", [])
    contact_email = cfg.get("contact_email", "enquiries@wtcabuja.com")
    contact_phone = cfg.get("contact_phone", "")
    response_days = cfg.get("response_days", "a few")
    slot_limit = cfg.get("slot_limit")
    hero_url = cfg.get("hero_url", "")
    logo_url = cfg.get("logo_url", "")
    greeting = f"Hello {greeting_name}," if greeting_name else _greeting(lead)

    # ── plain text ───────────────────────────────────────────────────────────
    text_lines = [
        greeting,
        "",
        f"Thank you for applying to {programme_name}. Your application has been "
        "received, and our admissions team will now review it against our "
        "eligibility criteria:",
    ]
    if eligibility:
        text_lines += ["", *[f"  - {item}" for item in eligibility]]
    text_lines += [
        "",
        "In the meantime, if you'd like to speak with a Trade Services "
        "representative — whether you have questions about eligibility, the "
        "programme structure, or the registration process — feel free to reach "
        "out:",
        "",
        f"  Email: {contact_email}",
    ]
    if contact_phone:
        text_lines.append(f"  Phone: {contact_phone}")
    text_lines += [
        "",
        f"We'll be in touch within {response_days} business days to confirm your "
        "status."
        + (f" Slots are limited to {slot_limit} companies for this cohort, so we "
           "encourage prompt follow-through." if slot_limit else ""),
        "",
        "Warm regards,",
        "WTC Abuja Team",
    ]
    text = "\n".join(text_lines) + "\n"

    # ── HTML ─────────────────────────────────────────────────────────────────
    eligibility_html = ""
    if eligibility:
        rows = "\n".join(
            f"""\
                  <tr>
                    <td style="vertical-align:top;width:20px;padding:0 0 10px;">
                      <span style="font-family:{_SANS};font-size:13px;color:{_C_GOLD_DK};">&#10003;</span>
                    </td>
                    <td style="vertical-align:top;padding:0 0 10px;">
                      <p style="font-family:{_SANS};font-size:13px;color:{_C_INK_SOFT};line-height:1.5;margin:0;">{item}</p>
                    </td>
                  </tr>"""
            for item in eligibility
        )
        eligibility_html = f"""\
              <p style="font-family:{_SANS};font-size:8px;font-weight:700;letter-spacing:0.18em;text-transform:uppercase;color:{_C_GOLD_DK};margin:0 0 14px;padding-top:22px;border-top:1px solid {_C_LINE};">Eligibility criteria</p>
              <table width="100%" cellpadding="0" cellspacing="0" border="0" role="presentation" style="margin-bottom:8px;">
{rows}
              </table>"""

    contact_html = f"""\
              <table width="100%" cellpadding="0" cellspacing="0" border="0" role="presentation" style="margin-top:8px;">
                <tr>
                  <td style="background:{_C_PANEL};border-radius:3px;padding:22px 24px;">
                    <p style="font-family:{_SANS};font-size:8px;font-weight:700;letter-spacing:0.14em;text-transform:uppercase;color:{_C_GOLD_DK};margin:0 0 8px;">Questions before we're in touch?</p>
                    <p style="font-family:{_SANS};font-size:12px;color:{_C_INK_SOFT};line-height:1.6;margin:0 0 6px;">Reach a Trade Services representative directly — eligibility, programme structure, or registration.</p>
                    <p style="font-family:{_SANS};font-size:13px;margin:0;">
                      <a href="mailto:{contact_email}" style="color:{_C_GOLD_DK};font-weight:600;text-decoration:none;">{contact_email}</a>
                      {f'&nbsp;&middot;&nbsp;<span style="color:{_C_INK_SOFT};">{contact_phone}</span>' if contact_phone else ""}
                    </p>
                  </td>
                </tr>
              </table>"""

    status_html = (
        f"We'll be in touch within {response_days} business days to confirm your status."
        + (
            f" Slots are limited to {slot_limit} companies for this cohort, so we "
            "encourage prompt follow-through."
            if slot_limit
            else ""
        )
    )

    body_html = f"""\
              <p style="font-family:{_SANS};font-size:15px;color:{_C_INK};line-height:1.6;margin:0 0 8px;">{greeting}</p>
              <p style="font-family:{_SANS};font-size:14px;color:{_C_INK_SOFT};line-height:1.7;margin:0 0 4px;">Thank you for applying to {programme_name}. Your application has been received, and our admissions team will now review it against our eligibility criteria:</p>
{eligibility_html}
{contact_html}
              <p style="font-family:{_SANS};font-size:12px;color:{_C_INK_SOFT};line-height:1.7;margin:20px 0 0;padding-top:20px;border-top:1px solid {_C_LINE};">{status_html}</p>"""

    _domain = (
        f'<a href="https://wtcabuja.com" style="color:{_C_GOLD};text-decoration:none;">'
        f"wtcabuja.com</a>"
    )
    footer_note = (
        f"You are receiving this because you applied through {_domain}.<br>"
        "WTC Abuja Trade Services &nbsp;&middot;&nbsp; Central Business District, Abuja, Nigeria"
    )
    html = _HEAD + _c_shell(
        tag="Application Received",
        heading_html="Your application<br>has been received.",
        body_html=body_html,
        contact_lead_in="Can't wait? Reach us directly.",
        footer_note_html=footer_note,
        hero_url=hero_url,
        logo_url=logo_url,
        footer_email=contact_email,
    ) + "</html>"
    return subject, html, text


async def send_application_confirmation(
    lead: Lead, campaign: Campaign, settings: Settings | None = None
) -> bool:
    """Best-effort application-confirmation email to the applicant. Never raises."""
    settings = settings or get_settings()
    cfg = (campaign.config or {}).get("application_confirmation", {})
    try:
        subject, html, text = build_application_confirmation_email(lead, campaign)
        ok = await send_campaign_email(
            to_email=lead.email,
            subject=subject,
            html=html,
            text=text,
            settings=settings,
            from_email=cfg.get("from_email") or None,
            from_name=cfg.get("from_name") or None,
            lead_id=lead.id,
            email_kind="application_confirmation",
        )

        second = (lead.responses or {}).get("second_participant") or {}
        second_email = second.get("email")
        if second_email:
            second_subject, second_html, second_text = build_application_confirmation_email(
                lead, campaign, greeting_name=second.get("first_name")
            )
            await send_campaign_email(
                to_email=second_email,
                subject=second_subject,
                html=second_html,
                text=second_text,
                settings=settings,
                from_email=cfg.get("from_email") or None,
                from_name=cfg.get("from_name") or None,
                lead_id=lead.id,
                email_kind="application_confirmation",
            )

        return ok
    except Exception:
        logger.exception("application confirmation email failed", lead_id=lead.id)
        return False


# ── Post-event "reconnect" broadcast (to lead) ────────────────────────────────
# A one-off follow-up to everyone captured at an event: a short recap (a single
# hosted image in the body — no attachment) plus an invitation to book a tour.
# Sent by scripts/send_nog_reconnect.py, not by any capture flow or job.


def build_reconnect_email(
    lead: Lead, campaign: Campaign, image_url: str
) -> tuple[str, str, str]:
    """(subject, html, text) for the post-event reconnect email sent to the visitor.

    The "recap" is a single hosted image dropped inline in the body (`image_url`),
    not an attachment. Deliberately minimal — no hero banner and no footer strip —
    because the recap image is itself a fully-branded graphic (its own WTC header,
    stats, and footer with contact/site), so the shell chrome would only duplicate
    it. Just a personal greeting + short copy on a white card, then the graphic.
    """
    first = (lead.first_name or "").strip()
    greeting = f"Dear {first}," if first else "Dear guest,"
    subject = (
        f"Good to reconnect after NOG, {first}"
        if first
        else "Good to reconnect after NOG"
    )

    intro = "Thanks for stopping by the WTC Abuja stand during NOG last week."
    recap = (
        "Below is a short recap of the week. We would be delighted to welcome you "
        "for a tour of WTC Abuja at your convenience. Please let us know a suitable "
        "date and time, and we'll be happy to make the necessary arrangements."
    )

    # ── plain text ───────────────────────────────────────────────────────────
    text = (
        f"{greeting}\n\n{intro}\n\n{recap}\n\n"
        f"See the recap: {image_url}\n\n"
        "Warm regards,\nThe WTC Abuja Team\nenquiries@wtcabuja.com\n"
    )

    # ── HTML body ────────────────────────────────────────────────────────────
    image_block = (
        f'<table width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'role="presentation" style="margin:8px 0 4px;"><tr><td>'
        f'<img src="{image_url}" width="504" '
        f'alt="World Trade Center Abuja — NOG week recap" '
        f'style="width:100%;max-width:504px;height:auto;display:block;border:0;'
        f'border-radius:3px;"></td></tr></table>'
        if image_url
        else ""
    )
    body_html = f"""\
              <p style="font-family:{_SANS};font-size:15px;color:{_C_INK};line-height:1.6;margin:0 0 8px;">{greeting}</p>
              <p style="font-family:{_SANS};font-size:14px;color:{_C_INK_SOFT};line-height:1.7;margin:0 0 18px;">{intro}</p>
              <p style="font-family:{_SANS};font-size:14px;color:{_C_INK_SOFT};line-height:1.7;margin:0 0 24px;">{recap}</p>
{image_block}
              <p style="font-family:{_SANS};font-size:14px;color:{_C_INK_SOFT};line-height:1.7;margin:24px 0 0;">Warm regards,<br><span style="color:{_C_INK};font-weight:600;">The WTC Abuja Team</span></p>"""

    html = _HEAD + f"""\
<body style="margin:0;padding:0;background:{_C_CREAM};">
  <table width="100%" cellpadding="0" cellspacing="0" border="0" role="presentation" style="background:{_C_CREAM};">
    <tr>
      <td align="center" style="padding:40px 16px;">
        <table width="600" cellpadding="0" cellspacing="0" border="0" role="presentation" style="max-width:600px;width:100%;">
          <tr>
            <td style="background:#ffffff;padding:40px 48px;border-radius:4px;">
{body_html}
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body></html>"""
    return subject, html, text


async def send_reconnect(
    lead: Lead,
    campaign: Campaign,
    image_url: str,
    settings: Settings | None = None,
    cc: list[str] | None = None,
) -> bool:
    """Best-effort post-event reconnect email to the visitor. Never raises.

    `cc` copies each send to the given address(es). On a bulk broadcast that means
    one copy per recipient, so only pass it when a monitoring copy is wanted (a cc
    duplicating the lead's own address is dropped by send_campaign_email).
    Tagged `email_kind="reconnect"` for open/click attribution.
    """
    settings = settings or get_settings()
    try:
        subject, html, text = build_reconnect_email(lead, campaign, image_url)
        return await send_campaign_email(
            to_email=lead.email,
            subject=subject,
            html=html,
            text=text,
            settings=settings,
            cc=cc,
            lead_id=lead.id,
            email_kind="reconnect",
        )
    except Exception:
        logger.exception("reconnect email failed", lead_id=lead.id)
        return False
