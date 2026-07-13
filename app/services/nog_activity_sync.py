"""NOG contact-activity sync: mirror per-contact outreach (calls / emails / meetings /
notes) into `contact_activity`, attributed to each contact's assigned owner.

NOG leads are Freshsales contacts with no deal, so the deal-centric syncs never see
them, and Freshsales has no bulk activity API — so we fan out per contact. To attribute
without a GET per contact, owner + prospect tier are resolved in bulk via
`filtered_search` buckets on the stable custom fields `cf_owner` / `cf_prospect_tier`
(the system `owner_id` drifts back to the API user on routine upserts, so cf_owner is the
trustworthy salesperson signal). Emails come from the contact conversations endpoint,
notes from the notes endpoint, and calls/meetings from the account-wide sales_activities
list matched back to NOG contacts. Idempotent — deduped by `source_key`, safe to re-run.
"""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.freshsales.client import FreshsalesClient
from app.freshsales.parsing import parse_iso_timestamp
from app.models.campaign import Campaign
from app.models.contact_activity import (
    ACTIVITY_CALL,
    ACTIVITY_EMAIL,
    ACTIVITY_MEETING,
    ACTIVITY_NOTE,
)
from app.models.lead import Lead
from app.repositories import contact_activity_repo

logger = get_logger(__name__)

NOG_SLUG = "nog-2026"
NOG_LEAD_SOURCE_ID = 17001007403
# cf_owner dropdown values that map to a real salesperson (from the field's choices).
OWNER_CHOICES = ["Jennifer Obute", "Clinton Osuji", "Karim"]
TIERS = ["Strategic", "Standard"]

# Bulky content fields on conversation/note records we don't need for activity counts.
_HEAVY = frozenset(
    {
        "html_content",
        "current_html_content",
        "display_content",
        "shrink_content",
        "attachments",
        "conversation_meta",
        "collab_context",
    }
)


async def _bucket_map(
    client: FreshsalesClient, attribute: str, values: list[str]
) -> dict[int, str]:
    """contact_id -> value, resolved in bulk via filtered_search on a custom field
    (avoids a GET per contact). Scoped to NOG-sourced contacts."""
    out: dict[int, str] = {}
    for value in values:
        page = 1
        seen = 0
        while True:
            body = await client.post(
                f"/crm/sales/api/filtered_search/contact?page={page}",
                {
                    "filter_rule": [
                        {
                            "attribute": "lead_source_id",
                            "operator": "is_in",
                            "value": [NOG_LEAD_SOURCE_ID],
                        },
                        {"attribute": attribute, "operator": "is_in", "value": [value]},
                    ]
                },
            )
            recs = body.get("contacts", [])
            if not recs:
                break
            for r in recs:
                out[int(r["id"])] = value
            seen += len(recs)
            total = body.get("meta", {}).get("total")
            if total is not None and seen >= total:
                break
            page += 1
    return out


async def _activity_type_map(client: FreshsalesClient) -> dict[int, str]:
    """sales_activity_type_id -> 'meeting' | 'call' (default call), from the selector."""
    body = await client.get("/crm/sales/api/selector/sales_activity_types")
    out: dict[int, str] = {}
    for t in body.get("sales_activity_types", []):
        name = (t.get("name") or "").lower()
        out[t.get("id")] = ACTIVITY_MEETING if "meet" in name else ACTIVITY_CALL
    return out


async def run_nog_activity_sync(
    session: AsyncSession, client: FreshsalesClient, *, limit: int | None = None
) -> dict[str, int]:
    """Refresh `contact_activity` for the NOG campaign's contacts. Returns counters."""
    campaign = (
        await session.execute(select(Campaign).where(Campaign.slug == NOG_SLUG))
    ).scalar_one_or_none()
    if campaign is None:
        logger.warning("nog activity sync: campaign not found", slug=NOG_SLUG)
        return {"contacts": 0}

    leads = (
        await session.execute(
            select(Lead).where(
                Lead.campaign_id == campaign.id, Lead.crm_contact_id.isnot(None)
            )
        )
    ).scalars().all()

    owners_by_name = {
        (u.get("display_name") or u.get("name")): u.get("id")
        for u in (await client.get_owners()).get("users", [])
    }
    owner_map = await _bucket_map(client, "cf_owner", OWNER_CHOICES)
    tier_map = await _bucket_map(client, "cf_prospect_tier", TIERS)

    counters = {"contacts": 0, "email": 0, "note": 0, "call": 0, "meeting": 0}
    contacts = [(int(lead.crm_contact_id), lead) for lead in leads]
    if limit is not None:
        contacts = contacts[:limit]
    nog_contact_ids = {cid for cid, _ in contacts}

    lead_by_contact = {cid: lead for cid, lead in contacts}

    def _base(cid: int) -> dict[str, Any]:
        owner_name = owner_map.get(cid)
        lead = lead_by_contact.get(cid)
        name = f"{lead.first_name or ''} {lead.last_name or ''}".strip() if lead else ""
        return {
            "campaign_id": campaign.id,
            "contact_id": cid,
            "contact_name": name or None,
            "owner_name": owner_name,
            "owner_id": owners_by_name.get(owner_name) if owner_name else None,
            "prospect_tier": tier_map.get(cid),
        }

    for cid, _lead in contacts:
        base = _base(cid)

        conv = await client.get_contact_conversations(cid)
        for e in conv.get("email_conversations", []):
            ts = parse_iso_timestamp(e.get("conversation_time"))
            if ts is None:
                continue
            await contact_activity_repo.upsert(
                session,
                {
                    **base,
                    "activity_type": ACTIVITY_EMAIL,
                    "direction": e.get("direction"),
                    "occurred_at": ts,
                    "subject": e.get("subject"),
                    "source_key": f"email:{e.get('id')}",
                    "raw": {k: v for k, v in e.items() if k not in _HEAVY},
                },
            )
            counters["email"] += 1

        notes = await client.get_contact_notes(cid)
        for n in notes.get("notes", []):
            ts = parse_iso_timestamp(n.get("created_at") or n.get("updated_at"))
            if ts is None:
                continue
            desc = (n.get("description") or "").strip()
            await contact_activity_repo.upsert(
                session,
                {
                    **base,
                    "activity_type": ACTIVITY_NOTE,
                    "direction": None,
                    "occurred_at": ts,
                    "subject": desc[:200] or None,
                    "source_key": f"note:{n.get('id')}",
                    "raw": {k: v for k, v in n.items() if k not in _HEAVY},
                },
            )
            counters["note"] += 1

        counters["contacts"] += 1
        await session.commit()

    # Calls / meetings: account-wide sales_activities, matched back to NOG contacts.
    type_map = await _activity_type_map(client)
    async for sa in client.iter_sales_activities():
        tgt = sa.get("targetable") or {}
        if tgt.get("type") != "Contact":
            continue
        cid = tgt.get("id")
        if cid not in nog_contact_ids:
            continue
        ts = parse_iso_timestamp(sa.get("created_at") or sa.get("start_date"))
        if ts is None:
            continue
        kind = type_map.get(sa.get("sales_activity_type_id"), ACTIVITY_CALL)
        await contact_activity_repo.upsert(
            session,
            {
                **_base(cid),
                "activity_type": kind,
                "direction": None,
                "occurred_at": ts,
                "subject": sa.get("title"),
                "source_key": f"sa:{sa.get('id')}",
                "raw": {k: v for k, v in sa.items() if k not in _HEAVY},
            },
        )
        counters[kind] += 1
    await session.commit()

    logger.info("nog activity sync complete", **counters)
    return counters
