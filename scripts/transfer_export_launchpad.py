"""Transfer Export Launchpad registrations from the existing campaign/leads
tables into the new Trade tables (trade_programs / trade_leads).

Export Launchpad Boot Camp 2026 was originally built on the generic
campaigns/leads machinery (like NOG/website), but Trade contacts have a
different direction (export-readiness screening) than workspace/residential
leads, so they're moving to dedicated Trade tables. This script is the
one-time (re-runnable) migration of what's already live there.

Each registration becomes ONE OR TWO `trade_leads` rows (primary + optional
2nd participant, linked by `registration_id`):
  * the PRIMARY participant comes straight off the `leads` row and already has
    a real Freshsales contact (`crm_contact_id`) — that link is preserved and
    the row lands `crm_sync_status=synced`, so it is NEVER re-pushed;
  * the 2ND PARTICIPANT (when present) is read from the nested
    `responses['second_participant']` object. Under the old campaign/leads
    flow this person was captured but NEVER pushed to Freshsales as their own
    contact — so this transfer is what first creates that contact. These rows
    land `crm_sync_status=pending`, picked up by the normal trade_crm_sync job.

Email open/click history (`email_events`, keyed to the primary's `lead_id`)
is re-pointed at the new primary trade_leads row via `trade_lead_id`, and the
same engagement rollup (opened_at/opened_count/clicked_materials) is copied
onto that row.

Idempotent: each transferred row's `registration_id` is deterministic
(`campaign-lead-<source lead id>`), so re-running skips registrations that
already have trade_leads rows instead of duplicating them.

Usage (run from backend/, against prod via Railway):

    # preview only — touches nothing:
    railway run uv run python scripts/transfer_export_launchpad.py

    # actually write to the database:
    railway run uv run python scripts/transfer_export_launchpad.py --commit

    # after a verified --commit, archive the source campaign:
    railway run uv run python scripts/transfer_export_launchpad.py --archive-source
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import update  # noqa: E402

from app.core.database import session_scope  # noqa: E402
from app.models.campaign import STATUS_ARCHIVED  # noqa: E402
from app.models.email_event import EmailEvent  # noqa: E402
from app.models.trade_lead import CRM_PENDING, CRM_SYNCED, TradeLead  # noqa: E402
from app.repositories import campaigns_repo, leads_repo, trade_repo  # noqa: E402

SOURCE_CAMPAIGN_SLUG = "export-launchpad-2026"
TRADE_PROGRAM_SLUG = "export-launchpad-2026"

# Registration-level fields that live in `responses` on the source lead and
# are duplicated onto BOTH participant rows of a registration — the names
# match the real production payload exactly (verified against live data),
# not just the public form's field labels.
_SHARED_RESPONSE_KEYS = (
    "company_founded", "industry_sector", "sector_specification", "sector_other",
    "ownership", "operating_currency", "fiscal_year_start", "employee_count",
    "sources_internationally", "source_countries", "sells_internationally",
    "sales_countries", "topics_of_interest", "consent_terms",
    "consent_data_processing", "consent_liability_waiver", "consent_photo_video",
    "cohort_date", "wtc_location", "registered_address", "city", "postal_code",
    "country",
)


def _registration_id(source_lead_id: int) -> str:
    return f"campaign-lead-{source_lead_id}"


def _shared_fields(responses: dict) -> dict:
    return {k: responses.get(k) for k in _SHARED_RESPONSE_KEYS}


def _build_primary(lead, program_id: int, shared: dict) -> TradeLead:
    return TradeLead(
        trade_program_id=program_id,
        registration_id=_registration_id(lead.id),
        participant_index=1,
        is_primary=True,
        first_name=lead.first_name,
        middle_name=lead.responses.get("middle_name"),
        last_name=lead.last_name,
        email=(lead.email or "").strip().lower(),
        phone=lead.phone or None,
        job_title=lead.job_title,
        company=lead.company,
        source="campaign_transfer",
        captured_at=lead.captured_at,
        responses=lead.responses,
        crm_sync_status=CRM_SYNCED,
        crm_synced_at=lead.crm_synced_at,
        crm_contact_id=lead.crm_contact_id,
        crm_error=lead.crm_error,
        opened_at=lead.pack_opened_at,
        opened_count=lead.pack_opened_count or 0,
        clicked_materials=lead.pack_clicked_materials,
        **shared,
    )


def _build_second_participant(
    lead, program_id: int, primary_email: str, shared: dict
) -> tuple[TradeLead | None, str | None]:
    """Returns (row, warning). `warning` is set (row still built) when the raw
    data can't be used as-is — e.g. the 2nd participant's email collides with
    the primary's (seen on a live test registration: same email typed for
    both) — a real per-program unique-email violation if written verbatim, and
    not a distinct contact for Freshsales either way, so the email is dropped
    (kept in `responses` for reference) rather than blocking the whole batch."""
    second = (lead.responses or {}).get("second_participant") or {}
    first_name = (second.get("first_name") or "").strip()
    last_name = (second.get("last_name") or "").strip()
    if not first_name and not last_name:
        return None, None  # per the locked rule: 2nd participant is optional, skip if empty
    email = (second.get("email") or "").strip().lower()
    warning = None
    if email and email == primary_email:
        warning = (
            f"2nd participant email '{email}' matches the primary's — dropping it "
            f"(kept in responses) to avoid a duplicate-email row"
        )
        email = ""
    row = TradeLead(
        trade_program_id=program_id,
        registration_id=_registration_id(lead.id),
        participant_index=2,
        is_primary=False,
        first_name=first_name or "—",
        last_name=last_name or "—",
        email=email,
        phone=second.get("phone") or None,
        job_title=second.get("job_title"),
        company=lead.company,
        source="campaign_transfer",
        responses=second,  # raw second_participant payload — preserves the dropped email
        crm_sync_status=CRM_PENDING,  # never synced under the old flow — push fresh
        **shared,
    )
    return row, warning


async def run(commit: bool) -> None:
    async with session_scope() as session:
        source_campaign = await campaigns_repo.get_by_slug(session, SOURCE_CAMPAIGN_SLUG)
        if source_campaign is None:
            print(f"Source campaign '{SOURCE_CAMPAIGN_SLUG}' not found — nothing to do.")
            return
        program = await trade_repo.get_program_by_slug(session, TRADE_PROGRAM_SLUG)
        if program is None:
            print(
                f"Trade program '{TRADE_PROGRAM_SLUG}' not found — run "
                "scripts/seed_trade_programs.py first."
            )
            return

        source_leads = await leads_repo.list_for_campaign(
            session, source_campaign.id, limit=100_000
        )

        to_create: list[tuple] = []  # (source_lead, primary, second_or_None, warning_or_None)
        already_transferred = 0
        for lead in source_leads:
            existing = await trade_repo.list_by_registration(
                session, _registration_id(lead.id)
            )
            if existing:
                already_transferred += 1
                continue
            shared = _shared_fields(lead.responses or {})
            primary = _build_primary(lead, program.id, shared)
            second, warning = _build_second_participant(
                lead, program.id, primary.email, shared
            )
            to_create.append((lead, primary, second, warning))

        print(f"Source campaign: {SOURCE_CAMPAIGN_SLUG} (id={source_campaign.id})")
        print(f"Trade program:   {TRADE_PROGRAM_SLUG} (id={program.id})")
        print(f"Source leads:    {len(source_leads)}")
        print(f"Already transferred (skipped): {already_transferred}")
        print(f"To transfer this run: {len(to_create)} registration(s)\n")

        for lead, primary, second, warning in to_create:
            tag = "2 participants" if second else "1 participant"
            print(
                f"  reg {primary.registration_id}: {primary.first_name} {primary.last_name} "
                f"<{primary.email or '-'}> [{lead.crm_sync_status}/{lead.crm_contact_id}] "
                f"({tag}, company={lead.company!r})"
            )
            if second:
                print(
                    f"      + 2nd: {second.first_name} {second.last_name} "
                    f"<{second.email or '-'}> [crm: never synced -> will push fresh]"
                )
            if warning:
                print(f"      ! WARNING: {warning}")

        if not commit:
            print("\nDRY RUN — nothing written. Re-run with --commit to transfer.")
            return

        created_primaries = created_seconds = repointed_events = 0
        for lead, primary, second, _warning in to_create:
            primary = await trade_repo.create_lead(session, primary)
            created_primaries += 1

            result = await session.execute(
                update(EmailEvent)
                .where(EmailEvent.lead_id == lead.id)
                .values(trade_lead_id=primary.id)
            )
            repointed_events += result.rowcount or 0

            if second:
                await trade_repo.create_lead(session, second)
                created_seconds += 1
        await session.commit()

        print(
            f"\nDone. primaries created={created_primaries} "
            f"2nd participants created={created_seconds} "
            f"email_events re-pointed={repointed_events}"
        )
        print(
            "Primaries kept their existing CRM contact link (crm_sync_status=synced, "
            "never re-pushed). 2nd participants land crm_sync_status=pending — the "
            "normal trade_crm_sync job will push them as new Freshsales contacts."
        )


async def archive_source() -> None:
    async with session_scope() as session:
        campaign = await campaigns_repo.get_by_slug(session, SOURCE_CAMPAIGN_SLUG)
        if campaign is None:
            print(f"Source campaign '{SOURCE_CAMPAIGN_SLUG}' not found.")
            return
        if campaign.status == STATUS_ARCHIVED:
            print(f"'{SOURCE_CAMPAIGN_SLUG}' is already archived.")
            return
        campaign.status = STATUS_ARCHIVED
        await campaigns_repo.update(session, campaign)
        print(f"Archived campaign '{SOURCE_CAMPAIGN_SLUG}' (data retained, not deleted).")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", action="store_true", help="Write to the DB (default: dry run)")
    parser.add_argument(
        "--archive-source",
        action="store_true",
        help="Archive the source campaign (run only after a verified --commit)",
    )
    args = parser.parse_args()
    if args.archive_source:
        asyncio.run(archive_source())
    else:
        asyncio.run(run(args.commit))


if __name__ == "__main__":
    main()
