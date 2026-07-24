"""Secondary top-up import for Trade program participants (CSV).

The PRIMARY way Export Launchpad contacts entered the Trade tables is
scripts/transfer_export_launchpad.py (moving what was already captured
through the campaign/leads flow). This script is for anything NOT covered by
that transfer — e.g. a supplementary contact list, or a 2nd participant a
company adds later by email (the application form explicitly allows this).

Expects a CSV with a header row containing at least: first_name, last_name,
email — plus any of the optional columns below. Unknown columns are ignored;
missing optional columns are left blank. One row = one participant. Rows are
deduped by (program, email) — a re-run merges onto the existing row instead
of creating a duplicate; existing rows are never overwritten (a spreadsheet
import is a thinner data source than the real form submission).

Usage (run from backend/, against prod via Railway):

    # preview only — touches nothing:
    railway run uv run python scripts/import_trade_leads.py --source contacts.csv

    # actually write to the database:
    railway run uv run python scripts/import_trade_leads.py --source contacts.csv --commit
"""

import argparse
import asyncio
import csv
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.database import session_scope  # noqa: E402
from app.models.trade_lead import TradeLead  # noqa: E402
from app.repositories import trade_repo  # noqa: E402

PROGRAM_SLUG = "export-launchpad-2026"

_REQUIRED_COLUMNS = ("first_name", "last_name", "email")
_OPTIONAL_COLUMNS = (
    "middle_name", "phone", "job_title", "company", "registered_address", "city",
    "postal_code", "country", "company_founded", "industry_sector",
    "sector_specification", "sector_other", "operating_currency",
    "fiscal_year_start", "employee_count", "sources_internationally",
    "sells_internationally", "cohort_date", "wtc_location",
)


def _clean_row(row: dict[str, str]) -> dict[str, str]:
    return {k: (v or "").strip() for k, v in row.items()}


def _read_rows(source: str) -> list[dict[str, str]]:
    with open(source, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        missing = [c for c in _REQUIRED_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise SystemExit(f"CSV is missing required column(s): {', '.join(missing)}")
        return [_clean_row(row) for row in reader]


async def run(source: str, commit: bool) -> None:
    rows = _read_rows(source)
    print(f"Read {len(rows)} row(s) from {source}\n")

    async with session_scope() as session:
        program = await trade_repo.get_program_by_slug(session, PROGRAM_SLUG)
        if program is None:
            print(
                f"Trade program '{PROGRAM_SLUG}' not found — run "
                "scripts/seed_trade_programs.py first."
            )
            return

        to_import: list[dict[str, str]] = []
        skipped: list[tuple[dict[str, str], str]] = []
        existing_count = 0
        for row in rows:
            email = row["email"].lower()
            if not row["first_name"] or not row["last_name"] or not email:
                skipped.append((row, "missing first_name/last_name/email"))
                continue
            existing = await trade_repo.get_by_program_email(session, program.id, email)
            if existing is not None:
                existing_count += 1
                continue
            to_import.append(row)

        print(f"Importable (new): {len(to_import)}")
        print(f"Already present (skipped, not overwritten): {existing_count}")
        print(f"Skipped (missing required fields): {len(skipped)}")
        for row, reason in skipped[:10]:
            print(f"    - {row.get('email') or row}: {reason}")

        if not commit:
            print("\nSample of parsed rows (first 8):")
            for row in to_import[:8]:
                print(f"  {row['first_name']} {row['last_name']} <{row['email']}>")
            print("\nDRY RUN — nothing written. Re-run with --commit to import.")
            return

        created = 0
        for row in to_import:
            lead = TradeLead(
                trade_program_id=program.id,
                registration_id=f"sheet-import-{uuid.uuid4().hex[:12]}",
                participant_index=1,
                is_primary=True,
                first_name=row["first_name"],
                middle_name=row.get("middle_name") or None,
                last_name=row["last_name"],
                email=row["email"].lower(),
                phone=row.get("phone") or None,
                job_title=row.get("job_title") or None,
                company=row.get("company") or None,
                registered_address=row.get("registered_address") or None,
                city=row.get("city") or None,
                postal_code=row.get("postal_code") or None,
                country=row.get("country") or None,
                company_founded=row.get("company_founded") or None,
                industry_sector=row.get("industry_sector") or None,
                sector_specification=row.get("sector_specification") or None,
                sector_other=row.get("sector_other") or None,
                operating_currency=row.get("operating_currency") or None,
                fiscal_year_start=row.get("fiscal_year_start") or None,
                employee_count=row.get("employee_count") or None,
                sources_internationally=row.get("sources_internationally") or None,
                sells_internationally=row.get("sells_internationally") or None,
                cohort_date=row.get("cohort_date") or None,
                wtc_location=row.get("wtc_location") or None,
                source="sheet_import",
                responses=row,
            )
            await trade_repo.create_lead(session, lead)
            created += 1

    print(
        f"\nDone. created={created}\n"
        "Imported rows are crm_sync_status=pending — the normal trade_crm_sync "
        "job pushes them to Freshsales as new contacts."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, help="Path to a CSV file")
    parser.add_argument("--commit", action="store_true", help="Write to the DB (default: dry run)")
    args = parser.parse_args()
    asyncio.run(run(args.source, args.commit))


if __name__ == "__main__":
    main()
