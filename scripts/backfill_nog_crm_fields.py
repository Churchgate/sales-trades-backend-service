"""One-off: push Source / Collateral Sent? / Lifecycle Stage-NOG Week to every
existing nog-2026 lead's Freshsales contact.

`lead_crm_sync.build_contact_payload` now sets these three fields for any nog-2026
lead it syncs (see that module), but the regular sync job only re-attempts
`pending`/`failed` leads — most NOG leads are already `synced`, so they won't pick
up the new fields on their own. This forces one full re-sync pass over the whole
campaign so every contact gets Source=NOG-Week-2026, and Collateral Sent?/Lifecycle
Stage reflecting each lead's CURRENT pack_delivery_status at run time.

Safe to re-run: upsert is idempotent (deduped by email), and re-running later will
just refresh Collateral Sent?/Lifecycle Stage as more packs go from pending -> sent.

Usage (from backend/, against prod via Railway):

    railway run uv run python scripts/backfill_nog_crm_fields.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.database import session_scope  # noqa: E402
from app.freshsales.client import FreshsalesClient  # noqa: E402
from app.models.campaign import Campaign  # noqa: E402
from app.models.lead import CRM_SYNCED, Lead  # noqa: E402
from app.services import lead_crm_sync  # noqa: E402

CAMPAIGN_SLUG = "nog-2026"


async def main() -> None:
    settings = get_settings()
    if not settings.freshsales_lead_sync_enabled:
        print("freshsales_lead_sync_enabled is OFF — aborting (nothing would persist).")
        return

    async with session_scope() as session:
        campaign = (
            await session.execute(select(Campaign).where(Campaign.slug == CAMPAIGN_SLUG))
        ).scalars().first()
        leads = (
            await session.execute(select(Lead).where(Lead.campaign_id == campaign.id))
        ).scalars().all()

        print(f"Re-syncing {len(leads)} {CAMPAIGN_SLUG} leads to Freshsales...")
        synced = failed = 0
        async with FreshsalesClient(settings) as client:
            for lead in leads:
                result = await lead_crm_sync.sync_lead(
                    session, lead, campaign, client=client, settings=settings
                )
                if result.crm_sync_status == CRM_SYNCED:
                    synced += 1
                else:
                    failed += 1
                    print(f"  ! {lead.email}: {result.crm_error}")

    print(f"\nDone. synced={synced}  failed={failed}")


if __name__ == "__main__":
    asyncio.run(main())
