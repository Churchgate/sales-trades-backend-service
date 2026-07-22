"""One-off: push ICP score (and, for website leads, the new "New" status) to
every already-synced lead's Freshsales contact.

`lead_crm_sync.build_contact_payload` now sets `cf_icp_score` for every lead, and
`contact_status_id` (New) for wtcabuja-website leads specifically — but the regular
sync job only re-attempts `pending`/`failed` leads, and almost every lead is
already `synced`, so they won't pick up either field on their own. This forces one
full re-sync pass across every campaign, following the same pattern as
scripts/backfill_nog_crm_fields.py (which did the equivalent one-off push for the
Source/Collateral Sent?/Lifecycle Stage fields).

Safe to re-run: upsert is idempotent (deduped by email), and leads scored after
this run will simply pick up cf_icp_score on their next regular sync anyway.

Usage (from backend/, against prod via Railway):

    railway run uv run python scripts/backfill_icp_crm_fields.py
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


async def main() -> None:
    settings = get_settings()
    if not settings.freshsales_lead_sync_enabled:
        print("freshsales_lead_sync_enabled is OFF — aborting (nothing would persist).")
        return

    async with session_scope() as session:
        campaigns = {
            c.id: c for c in (await session.execute(select(Campaign))).scalars().all()
        }
        leads = (
            await session.execute(select(Lead).where(Lead.crm_sync_status == CRM_SYNCED))
        ).scalars().all()

        print(f"Re-syncing {len(leads)} already-synced leads to Freshsales...")
        synced = failed = 0
        async with FreshsalesClient(settings) as client:
            for lead in leads:
                campaign = campaigns.get(lead.campaign_id)
                if campaign is None:
                    continue
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
