"""Pull Freshsales' own `last_contacted` signal back into our `triage_status`.

One-way: a rep who calls or emails a lead directly in Freshsales — bypassing our
dashboard's "Mark contacted" action entirely — leaves no trace in `triage_status`,
so the Hot Leads queue keeps surfacing someone who has, in fact, already been
worked. `last_contacted` (a system field Freshsales sets automatically whenever any
activity is logged against a contact) is the CRM's own ground truth for that.

Only ever advances a lead OUT of the default `new` state — never touches a lead a
human has already triaged (contacted/dismissed/snoozed) via our own dashboard, so
this can't fight with or reverse someone's own triage decision.
"""

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.freshsales import endpoints
from app.freshsales.client import FreshsalesClient
from app.models.lead import TRIAGE_CONTACTED
from app.repositories import leads_repo

logger = get_logger(__name__)

# Distinguishes an auto-advance here from a rep's own click in `triage_by`.
SYSTEM_TRIAGE_BY = "freshsales-sync"


async def sync_triage_from_crm(
    session: AsyncSession, client: FreshsalesClient, *, limit: int = 200
) -> int:
    """Advance `triage_status` to `contacted` for leads Freshsales shows as
    genuinely contacted. Returns the number of leads advanced."""
    leads = await leads_repo.list_untriaged_with_crm_contact(session, limit=limit)
    advanced = 0
    for lead in leads:
        try:
            data = await client.get(endpoints.contact_detail(int(lead.crm_contact_id)))
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "triage sync: contact fetch failed", lead_id=lead.id, error=str(exc)
            )
            continue
        contact = data.get("contact", data)
        if contact.get("last_contacted"):
            await leads_repo.set_triage(
                session, lead, status=TRIAGE_CONTACTED, by=SYSTEM_TRIAGE_BY
            )
            advanced += 1
    return advanced
