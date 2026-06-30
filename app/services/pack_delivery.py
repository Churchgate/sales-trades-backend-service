"""Email a captured lead the digital-pack materials they requested.

The "Send Me the Digital Pack" form promises the visitor their selected materials
by email ("we'll send materials right away"). Capture's only job is to never lose
a lead, so it saves the request `pending` and this service does the actual send —
either inline (best-effort, for immediacy) or via the scheduled
`pack_delivery_job`, exactly like `lead_crm_sync` pushes to the CRM.

Which labels are *deliverable* and where each asset lives is driven entirely by
the campaign's (dynamic) `config`:

    config["materials"]        -> the real material labels for this event
    config["materials_assets"] -> { label: [https://.../asset, ...] } download
                                   links — a list, since one material can be
                                   more than one file (e.g. "Office Floorplates"
                                   as two separate floorplate images). A bare
                                   string is also accepted for a single-file
                                   material, for config readability.

So a new event is still just a new campaign row — no code change. Anything the
visitor checked that isn't a real material with a configured asset (e.g. the
"WTC Abuja Updates & Private Invitations" newsletter pseudo-item, which is the
marketing opt-in, not a document) is simply not delivered here.
"""

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.models.campaign import Campaign
from app.models.lead import (
    PACK_FAILED,
    PACK_NOT_REQUESTED,
    PACK_PENDING,
    PACK_SENT,
    PACK_SKIPPED,
    Lead,
)
from app.repositories import campaigns_repo, leads_repo
from app.services import mailer

logger = get_logger(__name__)


def _asset_urls(value: object) -> list[str]:
    """Normalise a materials_assets entry to a list of URLs (string or list)."""
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, list):
        return [v for v in value if isinstance(v, str) and v]
    return []


def deliverable_materials(lead: Lead, campaign: Campaign) -> list[tuple[str, list[str]]]:
    """(label, [urls]) pairs this lead requested that have a configured asset.

    Filters the lead's raw `requested_materials` down to real materials for the
    campaign (config["materials"]) that also have at least one download link
    (config["materials_assets"]). Order follows the visitor's selection.
    """
    config = campaign.config or {}
    known = set(config.get("materials", []))
    assets: dict[str, object] = config.get("materials_assets", {})
    pairs: list[tuple[str, list[str]]] = []
    seen: set[str] = set()
    for label in lead.requested_materials or []:
        if label in seen or label not in known:
            continue
        urls = _asset_urls(assets.get(label))
        if urls:
            seen.add(label)
            pairs.append((label, urls))
    return pairs


def _real_materials_requested(lead: Lead, campaign: Campaign) -> bool:
    """Did the visitor ask for any real document (ignoring newsletter pseudo-items)?"""
    known = set((campaign.config or {}).get("materials", []))
    return any(label in known for label in (lead.requested_materials or []))


def build_pack_email(
    lead: Lead, campaign: Campaign, materials: list[tuple[str, list[str]]]
) -> tuple[str, str, str]:
    """(subject, html, text) for the digital-pack email. Copy is overridable via
    config["digital_pack"] = {
        "subject": ..., "intro": ..., "logo_url": ...,
        "contact_email": ..., "contact_phone": ...,
    } — logo_url/contact_email/contact_phone are all optional and only render
    once set (added to the live config as they're available, no code change
    needed).

    A material with more than one file (e.g. two floorplate images) gets one
    download button per file under a single heading.
    """
    pack_cfg = (campaign.config or {}).get("digital_pack", {})
    event_name = campaign.name
    subject = pack_cfg.get("subject", f"Your {event_name} digital pack")
    intro = pack_cfg.get(
        "intro",
        "Thank you for visiting us. As requested, here are your materials to "
        "download:",
    )
    logo_url = pack_cfg.get("logo_url")
    contact_email = pack_cfg.get("contact_email")
    contact_phone = pack_cfg.get("contact_phone")
    greeting = f"Hello {lead.first_name}," if lead.first_name else "Hello,"

    def _file_label(index: int, total: int) -> str:
        return f"Download {index + 1}" if total > 1 else "Download"

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

    _button_style = (
        "display:inline-block;background:#c79a3a;color:#15181e;text-decoration:none;"
        "font-weight:600;font-size:13px;padding:8px 14px;border-radius:8px;"
        "margin:4px 8px 0 0"
    )
    cards = "\n".join(
        f"""\
  <div style="background:#f4f6f8;border-radius:8px;padding:16px;margin:12px 0">
    <div style="font-weight:600;margin-bottom:4px">{label}</div>
    {"".join(
        f'<a href="{url}" style="{_button_style}">{_file_label(i, len(urls))}</a>'
        for i, url in enumerate(urls)
    )}
  </div>"""
        for label, urls in materials
    )

    contact_lines = []
    if contact_email:
        contact_lines.append(
            f'<a href="mailto:{contact_email}" style="color:#c79a3a;text-decoration:none">'
            f"{contact_email}</a>"
        )
    if contact_phone:
        contact_lines.append(contact_phone)
    contact_block = (
        f"""\
  <p style="color:#666;font-size:13px;margin-top:20px">
    Questions? We're happy to help — {" · ".join(contact_lines)}
  </p>"""
        if contact_lines
        else ""
    )

    logo_block = (
        f"""\
  <div style="text-align:center;margin-bottom:20px">
    <img src="{logo_url}" alt="{event_name}" style="max-width:220px;height:auto" />
  </div>"""
        if logo_url
        else ""
    )

    html = f"""\
<div style="font-family:Arial,Helvetica,sans-serif;max-width:560px;margin:auto;color:#15181e">
{logo_block}
  <h2 style="margin-bottom:4px">Your digital pack</h2>
  <p>{greeting}</p>
  <p>{intro}</p>
{cards}
{contact_block}
  <p style="color:#666;font-size:13px;margin-top:16px">
    Sent on behalf of {event_name}. If a link doesn't open, reply to this email
    and we'll help.
  </p>
</div>
"""
    return subject, html, text


async def deliver_pack(
    session: AsyncSession,
    lead: Lead,
    campaign: Campaign,
    *,
    settings: Settings | None = None,
) -> Lead:
    """Email one lead their requested materials, recording the outcome. Never raises.

    Outcomes mirror the CRM lifecycle: `not_requested` (nothing deliverable),
    `sent`, `skipped` (email transport unconfigured — retried once configured),
    `failed` (transport/config error — retried by the job).
    """
    settings = settings or get_settings()

    materials = deliverable_materials(lead, campaign)
    if not materials:
        # No real document with a configured asset. If they asked for a real
        # material but no asset is set up yet, that's a config gap worth retrying
        # once the link is added; otherwise there's genuinely nothing to send.
        if _real_materials_requested(lead, campaign):
            lead.pack_delivery_status = PACK_FAILED
            lead.pack_delivery_error = "no download link configured for requested materials"
        else:
            lead.pack_delivery_status = PACK_NOT_REQUESTED
            lead.pack_delivery_error = None
        return await leads_repo.update(session, lead)

    if not settings.sendgrid_api_key:
        lead.pack_delivery_status = PACK_SKIPPED
        lead.pack_delivery_error = "email transport not configured"
        return await leads_repo.update(session, lead)

    subject, html, text = build_pack_email(lead, campaign, materials)
    sent = await mailer.send_email(
        to_email=lead.email,
        subject=subject,
        html=html,
        text=text,
        settings=settings,
        from_email=settings.event_mail_from_email,
        from_name=settings.event_mail_from_name,
    )
    if sent:
        lead.pack_delivery_status = PACK_SENT
        lead.pack_delivered_at = datetime.now(UTC)
        lead.pack_delivered_materials = [label for label, _urls in materials]
        lead.pack_delivery_error = None
    else:
        lead.pack_delivery_status = PACK_FAILED
        lead.pack_delivery_error = "email send failed"
    return await leads_repo.update(session, lead)


async def deliver_pending(session: AsyncSession, *, limit: int = 200) -> int:
    """Send all pending/failed packs (across campaigns). Returns #delivered this run."""
    leads = await leads_repo.list_pending_pack_delivery(
        session, statuses=[PACK_PENDING, PACK_FAILED], limit=limit
    )
    if not leads:
        return 0

    campaigns: dict[int, Campaign] = {}
    delivered = 0
    for lead in leads:
        campaign = campaigns.get(lead.campaign_id)
        if campaign is None:
            campaign = await campaigns_repo.get(session, lead.campaign_id)
            if campaign is None:
                continue
            campaigns[lead.campaign_id] = campaign
        result = await deliver_pack(session, lead, campaign, settings=get_settings())
        if result.pack_delivery_status == PACK_SENT:
            delivered += 1
    return delivered
