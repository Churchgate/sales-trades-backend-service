"""One-time cleanup of Trade test data — both from the dashboard DB and the
live Freshsales CRM.

While building and verifying the Export Launchpad Trade flow (transfer
script, the new /register + /eligibility public endpoints), a number of
test/QA registrations landed in `trade_leads` and, because CRM sync was live
at the time some of them were created, several were ALSO pushed as real
Freshsales contacts. This script removes everything except the one genuine
registration (Amaka Eze / campaign-lead-541) from both places.

`KEEP_REGISTRATION_IDS` is the allow-list — everything else on the program is
deleted. Deletes, in order (FK-safe): email_events rows pointing at the
doomed trade_leads, then trade_documents, then trade_leads, then (if
--commit) the matching Freshsales contacts by crm_contact_id.

Usage (run from backend/, against prod via Railway):

    # preview only — touches nothing:
    railway run uv run python scripts/cleanup_trade_test_data.py

    # actually delete from the DB and Freshsales:
    railway run uv run python scripts/cleanup_trade_test_data.py --commit
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import delete, select  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.database import session_scope  # noqa: E402
from app.freshsales.client import FreshsalesClient  # noqa: E402
from app.models.email_event import EmailEvent  # noqa: E402
from app.models.trade_document import TradeDocument  # noqa: E402
from app.models.trade_lead import TradeLead  # noqa: E402

KEEP_REGISTRATION_IDS = {"campaign-lead-541"}  # Amaka Eze — the one real registration


async def run(commit: bool) -> None:
    async with session_scope() as session:
        leads = list((await session.execute(select(TradeLead))).scalars().all())
        doomed = [lead for lead in leads if lead.registration_id not in KEEP_REGISTRATION_IDS]

        if not doomed:
            print("Nothing to clean up — no leads outside the keep-list.")
            return

        doomed_ids = [lead.id for lead in doomed]
        crm_contact_ids = [lead.crm_contact_id for lead in doomed if lead.crm_contact_id]

        print(f"Keeping registrations: {sorted(KEEP_REGISTRATION_IDS)}")
        print(f"\n{len(doomed)} trade_leads row(s) to delete:")
        for lead in doomed:
            print(
                f"  id={lead.id} registration={lead.registration_id} "
                f"{lead.first_name} {lead.last_name} <{lead.email or '(no email)'}> "
                f"crm_contact_id={lead.crm_contact_id}"
            )
        print(f"\n{len(crm_contact_ids)} Freshsales contact(s) to delete: {crm_contact_ids}")

        if not commit:
            print("\nDry run — pass --commit to actually delete.")
            return

        events_result = await session.execute(
            delete(EmailEvent).where(EmailEvent.trade_lead_id.in_(doomed_ids))
        )
        docs_result = await session.execute(
            delete(TradeDocument).where(
                TradeDocument.registration_id.notin_(KEEP_REGISTRATION_IDS)
            )
        )
        leads_result = await session.execute(delete(TradeLead).where(TradeLead.id.in_(doomed_ids)))
        await session.commit()
        print(
            f"\nDeleted {leads_result.rowcount} trade_leads, "
            f"{docs_result.rowcount} trade_documents, "
            f"{events_result.rowcount} email_events."
        )

    if crm_contact_ids:
        settings = get_settings()
        deleted, failed = 0, []
        async with FreshsalesClient(settings) as client:
            for contact_id in crm_contact_ids:
                try:
                    await client.delete_contact(int(contact_id))
                    deleted += 1
                except Exception as exc:  # noqa: BLE001 — report and continue
                    failed.append((contact_id, str(exc)))
        print(f"Deleted {deleted}/{len(crm_contact_ids)} Freshsales contacts.")
        if failed:
            print("Failed:")
            for contact_id, error in failed:
                print(f"  {contact_id}: {error}")


if __name__ == "__main__":
    asyncio.run(run(commit="--commit" in sys.argv))
