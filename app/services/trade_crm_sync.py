"""Push Trade participants into Freshsales as contacts (best-effort, deduped).

Direct analogue of services/lead_crm_sync.py, with one important difference:
each PARTICIPANT is its own Freshsales contact (a registration with two
participants pushes two contacts), not one contact per registration.

Only rows with crm_sync_status='pending'/'failed' are ever pushed here. Rows
transferred in from the existing export-launchpad-2026 campaign primaries
arrive already 'synced' with their real crm_contact_id and are never
re-pushed — see scripts/transfer_export_launchpad.py. Second participants,
however, were never synced under the old campaign/leads flow, so their first
transfer through this module is also their first real CRM push.
"""

from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.freshsales.client import FreshsalesClient
from app.models.trade_lead import CRM_FAILED, CRM_SKIPPED, CRM_SYNCED, TradeLead
from app.models.trade_program import TradeProgram
from app.repositories import trade_repo

logger = get_logger(__name__)

# Freshsales requires both of these system fields on every contact (see the
# module comment in lead_crm_sync.py for the incident history). Trade shares
# the same "Inquiry Stage" starting point as every other new capture.
_INQUIRY_STAGE_ID = 18006136123
# Falls back to the website lead source until a dedicated Trade source is
# created/verified live via GET /crm/sales/api/selector/lead_sources.
_WEBSITE_LEAD_SOURCE_ID = 17001006640


def build_contact_payload(lead: TradeLead, program: TradeProgram) -> dict[str, Any]:
    """Map a TradeLead (one participant) to a Freshsales contacts/upsert body."""
    contact: dict[str, Any] = {
        "first_name": lead.first_name,
        "last_name": lead.last_name,
        "emails": [{"value": lead.email, "is_primary": True}],
        "mobile_number": lead.phone,
        "job_title": lead.job_title,
        "tags": lead.tags or [],
        "lead_source_id": _WEBSITE_LEAD_SOURCE_ID,
        "lifecycle_stage_id": _INQUIRY_STAGE_ID,
        "custom_field": {
            "cf_company": lead.company,
            "cf_campaign": program.slug,
            "cf_country": lead.country,
            "cf_industry_sector": lead.industry_sector,
            "cf_company_founded": lead.company_founded,
            "cf_employee_count": lead.employee_count,
            "cf_sources_internationally": lead.sources_internationally,
            "cf_sells_internationally": lead.sells_internationally,
            "cf_topics_of_interest": ", ".join(lead.topics_of_interest or []),
            # Placeholder for the (deferred) eligibility-document phase.
            "cf_eligibility_status": lead.eligibility_status,
        },
    }
    return {"unique_identifier": {"emails": lead.email}, "contact": contact}


async def sync_trade_lead(
    session: AsyncSession,
    lead: TradeLead,
    program: TradeProgram,
    *,
    client: FreshsalesClient,
    settings: Settings | None = None,
) -> TradeLead:
    """Push one participant to Freshsales, recording the outcome. Never raises."""
    settings = settings or get_settings()
    if not settings.freshsales_lead_sync_enabled:
        lead.crm_sync_status = CRM_SKIPPED
        return await trade_repo.update_lead(session, lead)

    try:
        contact = await client.upsert_contact(build_contact_payload(lead, program))
        contact_id = contact.get("id")
        lead.crm_contact_id = str(contact_id) if contact_id is not None else None
        lead.crm_sync_status = CRM_SYNCED
        lead.crm_synced_at = datetime.now(UTC)
        lead.crm_error = None
    except httpx.HTTPStatusError as exc:
        detail = f"{exc} | body: {exc.response.text}"
        logger.warning("trade lead crm sync failed", lead_id=lead.id, error=detail)
        lead.crm_sync_status = CRM_FAILED
        lead.crm_error = detail[:500]
    except Exception as exc:  # noqa: BLE001 — record and move on; job retries later
        logger.warning("trade lead crm sync failed", lead_id=lead.id, error=str(exc))
        lead.crm_sync_status = CRM_FAILED
        lead.crm_error = str(exc)[:500]
    return await trade_repo.update_lead(session, lead)


async def sync_pending_trade(session: AsyncSession, *, limit: int = 200) -> int:
    """Push all pending/failed trade participants. Returns #synced this run."""
    settings = get_settings()
    if not settings.freshsales_lead_sync_enabled:
        return 0

    leads = await trade_repo.list_pending_crm_sync(
        session, statuses=[CRM_FAILED, "pending"], limit=limit
    )
    if not leads:
        return 0

    programs: dict[int, TradeProgram] = {}
    synced = 0
    async with FreshsalesClient(settings) as client:
        for lead in leads:
            program = programs.get(lead.trade_program_id)
            if program is None:
                program = await trade_repo.get_program(session, lead.trade_program_id)
                if program is None:
                    continue
                programs[lead.trade_program_id] = program
            result = await sync_trade_lead(
                session, lead, program, client=client, settings=settings
            )
            if result.crm_sync_status == CRM_SYNCED:
                synced += 1
    return synced
