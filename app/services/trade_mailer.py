# ruff: noqa: E501
"""Trade program applicant emails (Export Launchpad and future programs).

Ported from campaign_mailer.py's build_application_confirmation_email /
send_application_confirmation (PR #69) for the new Trade capture flow.
Reuses that module's private HTML template helpers rather than duplicating
~150 lines of shared markup — Trade emails should look identical to the
campaign-era ones.

Simpler than the campaign version in one respect: a Trade registration's
second participant is already its own TradeLead row with real first_name/
email fields, not a nested `responses.second_participant` dict a greeting
has to be hacked around — so this sends each participant their own email
directly off their own row.

Known gap: unlike campaign_mailer.send_campaign_email, these sends omit the
lead_id/email_kind SendGrid custom_args, so opens/clicks aren't attributed
back to a trade_leads row yet (email_events.lead_id is a NOT NULL FK to
`leads`, not `trade_leads` — reusing it here would either misattribute to an
unrelated Lead row or fail the FK). Revisit alongside the eligibility/upload
phase if Trade email engagement tracking is needed.
"""

from datetime import UTC, datetime

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.models.trade_lead import TradeLead
from app.models.trade_program import TradeProgram
from app.services.campaign_mailer import (
    _BG,
    _C_GOLD,
    _C_GOLD_DK,
    _C_INK,
    _C_INK_SOFT,
    _C_LINE,
    _C_PANEL,
    _DIM,
    _DIVIDER,
    _GOLD,
    _HEAD,
    _INNER,
    _LABEL_GRAY,
    _MUTED,
    _SANS,
    _WHITE,
    _c_shell,
    _divider,
    _label,
    _wrap,
    _wtc_header,
    send_campaign_email,
)

logger = get_logger(__name__)


def _greeting(lead: TradeLead) -> str:
    return f"Hello {lead.first_name}," if lead.first_name else "Hello,"


def build_application_confirmation_email(
    lead: TradeLead, program: TradeProgram
) -> tuple[str, str, str]:
    """(subject, html, text) confirming a received application to one participant.

    Copy lives in program.config["application_confirmation"] — same shape as
    the campaign version: subject, eligibility criteria list, contact email/
    phone, response_days. Falls back to Export Launchpad's own defaults so a
    misconfigured program still sends something sane rather than erroring.
    """
    config = program.config or {}
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
    greeting = _greeting(lead)

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
        # A checkmark next to each item would read as "confirmed" — but these
        # haven't been reviewed yet at this point (the confirmation email
        # fires on submission, before any review happens), so that combined
        # with a "criteria" heading is a tautology: telling the applicant
        # they've already met requirements nobody has checked. A neutral
        # bullet + "what we'll review" framing avoids implying an outcome.
        rows = "\n".join(
            f"""\
                  <tr>
                    <td style="vertical-align:top;width:20px;padding:0 0 10px;">
                      <span style="font-family:{_SANS};font-size:13px;color:{_C_GOLD_DK};">&#8226;</span>
                    </td>
                    <td style="vertical-align:top;padding:0 0 10px;">
                      <p style="font-family:{_SANS};font-size:13px;color:{_C_INK_SOFT};line-height:1.5;margin:0;">{item}</p>
                    </td>
                  </tr>"""
            for item in eligibility
        )
        eligibility_html = f"""\
              <p style="font-family:{_SANS};font-size:8px;font-weight:700;letter-spacing:0.18em;text-transform:uppercase;color:{_C_GOLD_DK};margin:0 0 14px;padding-top:22px;border-top:1px solid {_C_LINE};">What we'll review</p>
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
    # The hero photo has "EXPORT LAUNCHPAD BOOT CAMP" baked into the image
    # itself, so overlaying our own heading text (and the small gold "World
    # Trade Center · Abuja" label — same text the photo/logo already carry)
    # on top of it collides into an unreadable mess. Only show them when
    # there's no hero image to clash with (the plain dark banner fallback
    # still needs its own text).
    #
    # Dropping both also shrinks the header's content height, and since the
    # hero is `background-size:cover`, a shorter header crops more of the
    # photo off the bottom — clipping the image's own baked-in text. The
    # photo is 1225x613 (verified via PIL); at the email's 600px width
    # that's ~300px tall uncropped, so the spacer is widened to compensate
    # for the label+heading no longer contributing height (~208px spacer +
    # ~92px of padding/logo row ≈ 300px total).
    heading_html = "" if hero_url else "Your application<br>has been received."
    hero_spacer_height = 208 if hero_url else 88
    html = _HEAD + _c_shell(
        tag="Application Received",
        heading_html=heading_html,
        body_html=body_html,
        contact_lead_in="Can't wait? Reach us directly.",
        footer_note_html=footer_note,
        hero_url=hero_url,
        logo_url=logo_url,
        footer_email=contact_email,
        hero_spacer_height=hero_spacer_height,
        hero_show_label=not hero_url,
    ) + "</html>"
    return subject, html, text


async def send_application_confirmation(
    participants: list[TradeLead], program: TradeProgram, settings: Settings | None = None
) -> bool:
    """Best-effort application-confirmation email to every participant with an
    email address (primary always has one; a 2nd participant may not). Never
    raises. Returns True if at least the primary's send succeeded."""
    settings = settings or get_settings()
    cfg = (program.config or {}).get("application_confirmation", {})
    ok = False
    for i, lead in enumerate(participants):
        if not lead.email:
            continue
        try:
            subject, html, text = build_application_confirmation_email(lead, program)
            sent = await send_campaign_email(
                to_email=lead.email,
                subject=subject,
                html=html,
                text=text,
                settings=settings,
                from_email=cfg.get("from_email") or None,
                from_name=cfg.get("from_name") or None,
            )
            if i == 0:
                ok = sent
        except Exception:
            logger.exception("trade application confirmation email failed", lead_id=lead.id)
    return ok


# ── Internal "New Registration" notification (to Trade Services) ───────────────
#
# Adapted from campaign_mailer.build_lead_notification_email/send_lead_notification
# (the "New Lead" email sent to enquiries@wtcabuja.com for regular campaign
# leads) — same dark-card visual style, but a distinct recipient
# (program.config["lead_notification"]["to_email"], not the shared
# settings.campaign_notification_email) and Trade-specific fields (company/
# sector/topics, both participants, required-documents checklist) instead of
# residential viewing/materials fields.


def build_lead_notification_email(
    participants: list[TradeLead], program: TradeProgram
) -> tuple[str, str, str]:
    """(subject, html, text) for the internal new-registration notification."""
    primary = next((p for p in participants if p.is_primary), participants[0])
    second = next((p for p in participants if not p.is_primary), None)
    full_name = " ".join(filter(None, [primary.first_name, primary.last_name])) or "Unknown"
    company = primary.company or "Not provided"
    job_title = primary.job_title or "Not provided"
    sector = primary.industry_sector or "Not provided"
    employees = primary.employee_count or "Not provided"
    topics = ", ".join(primary.topics_of_interest or []) or "None selected"
    captured_at = (
        primary.captured_at.astimezone(UTC).strftime("%-d %B %Y, %H:%M")
        if primary.captured_at
        else datetime.now(UTC).strftime("%-d %B %Y, %H:%M")
    )
    required_docs = [
        d.get("label", d.get("key"))
        for d in (program.config or {}).get("required_documents", [])
        if d.get("required")
    ]

    subject = f"New Export Launchpad Application: {full_name} — {company}"

    # ── plain text ───────────────────────────────────────────────────────────
    second_text = (
        f"{second.first_name} {second.last_name} <{second.email or 'no email'}>"
        if second
        else "None"
    )
    text = (
        f"New Export Launchpad Application — {program.name}\n"
        f"{'=' * 50}\n\n"
        f"Name:            {full_name}\n"
        f"Company:         {company}\n"
        f"Title:           {job_title}\n"
        f"Email:           {primary.email}\n"
        + (f"Phone:           {primary.phone}\n" if primary.phone else "")
        + f"Industry sector: {sector}\n"
        f"Employees:       {employees}\n"
        f"Topics:          {topics}\n"
        f"2nd participant: {second_text}\n\n"
        f"Required documents outstanding until submitted: {', '.join(required_docs) or 'None'}\n\n"
        f"Registration ID: {primary.registration_id}\n"
        f"Captured: {captured_at} via wtcabuja.com\n"
    )

    # ── HTML ─────────────────────────────────────────────────────────────────
    phone_line = (
        f'<div style="color:{_GOLD};font-size:14px;margin-top:6px">&#128241; {primary.phone}</div>'
        if primary.phone
        else ""
    )
    second_html = (
        f'<div style="color:{_MUTED};font-size:14px">{second.first_name} {second.last_name} '
        f'&lt;{second.email or "no email"}&gt;</div>'
        if second
        else f'<div style="color:{_LABEL_GRAY};font-size:14px">None</div>'
    )
    docs_html = (
        "".join(
            f'<div style="color:{_MUTED};font-size:14px;margin-bottom:6px">'
            f'<span style="color:{_GOLD};margin-right:10px">&#10022;</span>{d}</div>'
            for d in required_docs
        )
        if required_docs
        else f'<div style="color:{_LABEL_GRAY};font-size:14px">None configured</div>'
    )

    body = f"""\
{_wtc_header("New Export Launchpad Application")}
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
                {_label("SECTOR")}
                <div style="color:{_MUTED};font-size:14px">{sector}</div>
              </td>
            </tr>
          </table>
          {_divider()}
          <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:20px">
            <tr>
              <td width="50%" style="vertical-align:top;padding-right:16px">
                {_label("EMPLOYEES")}
                <div style="color:{_MUTED};font-size:14px">{employees}</div>
              </td>
              <td width="50%" style="vertical-align:top;padding-left:16px">
                {_label("TOPICS OF INTEREST")}
                <div style="color:{_MUTED};font-size:14px">{topics}</div>
              </td>
            </tr>
          </table>
          {_divider()}
          {_label("CONTACT")}
          <div style="color:{_GOLD};font-size:14px"><a href="mailto:{primary.email}" style="color:{_GOLD};text-decoration:none">&#9993; {primary.email}</a></div>
          {phone_line}
          <div style="margin-top:20px">
            {_label("2ND PARTICIPANT")}
            {second_html}
          </div>
          <div style="background:{_INNER};border-radius:10px;padding:20px 24px;margin:20px 0">
            {_label("REQUIRED DOCUMENTS")}
            {docs_html}
          </div>
          <div style="border-top:1px solid {_DIVIDER};padding-top:16px;margin-top:4px">
            {_label("REGISTRATION ID")}
            <div style="color:{_LABEL_GRAY};font-size:12px;font-family:monospace">{primary.registration_id}</div>
          </div>
        </td></tr>
      </table>
      <table width="100%" cellpadding="0" cellspacing="0">
        <tr><td style="background:{_BG};border-top:1px solid {_DIVIDER};padding:22px 40px;text-align:center">
          <div style="color:{_DIM};font-size:12px;margin-bottom:4px">Captured via wtcabuja.com Export Launchpad application form</div>
          <div style="color:{_DIM};font-size:12px;margin-bottom:14px">{captured_at} &middot; {program.name}</div>
          <div style="color:{_GOLD};font-size:9px;font-weight:700;letter-spacing:0.2em;text-transform:uppercase">WORLD TRADE CENTER ABUJA</div>
        </td></tr>
      </table>"""

    html = _wrap(body)
    return subject, html, text


async def send_lead_notification(
    participants: list[TradeLead], program: TradeProgram, settings: Settings | None = None
) -> bool:
    """Fire-and-forget internal notification to the Trade Services team on a
    genuinely new registration. Gated by program.config["lead_notification"]
    (mirrors campaigns' per-campaign "lead_notification" flag) — absent or
    falsy skips silently. Never raises."""
    settings = settings or get_settings()
    cfg = (program.config or {}).get("lead_notification") or {}
    if not cfg.get("enabled"):
        return False
    to_email = cfg.get("to_email")
    if not to_email:
        return False

    try:
        subject, html, text = build_lead_notification_email(participants, program)
        return await send_campaign_email(
            to_email=to_email, subject=subject, html=html, text=text, settings=settings
        )
    except Exception:
        logger.exception(
            "trade lead notification email failed", registration_id=participants[0].registration_id
        )
        return False
