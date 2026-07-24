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

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.models.trade_lead import TradeLead
from app.models.trade_program import TradeProgram
from app.services.campaign_mailer import (
    _C_GOLD,
    _C_GOLD_DK,
    _C_INK,
    _C_INK_SOFT,
    _C_LINE,
    _C_PANEL,
    _HEAD,
    _SANS,
    _c_shell,
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
