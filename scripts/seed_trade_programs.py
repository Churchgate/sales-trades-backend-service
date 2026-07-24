"""Seed/update Trade program rows. Idempotent — safe to re-run.

Run from backend/, against prod via Railway:

    railway run uv run python scripts/seed_trade_programs.py
"""

import asyncio
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.core.database import session_scope  # noqa: E402
from app.models.trade_program import KIND_BOOT_CAMP, STATUS_ACTIVE, TradeProgram  # noqa: E402

# Required-document list for the (deferred) eligibility-submission phase —
# collected via wtcabuja.com itself, not a dashboard-minted link. Carried in
# config now so the later phase has a single place to read it from.
_EXPORT_LAUNCHPAD_REQUIRED_DOCUMENTS = [
    {"key": "cac_certificate", "label": "CAC Certificate", "required": True},
    {"key": "logo", "label": "Company Logo", "required": True},
    {"key": "company_profile", "label": "Company Profile / Brochure", "required": False},
    {"key": "business_plan", "label": "Business Plan", "required": False},
]

_EXPORT_LAUNCHPAD_CONFIG = {
    "email_template": "export_launchpad_confirmation",  # distinct from the campaign templates
    "required_documents": _EXPORT_LAUNCHPAD_REQUIRED_DOCUMENTS,
    "company_founded_options": [
        "Less than 2 years", "2-5 years", "5-9 years", "10-20 years", "More than 20 years",
    ],
    "topics_of_interest_options": [
        "E-Commerce", "ESG", "Finance and Tax", "Managing a Global Workforce",
        "Tariff Playbook", "Supply Chain", "Export Bootcamp", "Other",
    ],
}

PROGRAMS: list[dict] = [
    {
        "slug": "export-launchpad-2026",
        "name": "Export Launchpad Boot Camp 2026 — First Cohort",
        "kind": KIND_BOOT_CAMP,
        "status": STATUS_ACTIVE,
        "starts_on": date(2026, 8, 20),  # cohort_date seen on live registrations
        "ends_on": None,
        "timezone": "Africa/Lagos",
        "config": _EXPORT_LAUNCHPAD_CONFIG,
    },
]

_FIELDS = ("name", "kind", "status", "starts_on", "ends_on", "timezone", "config")


async def seed_trade_programs() -> None:
    async with session_scope() as session:
        for spec in PROGRAMS:
            existing = (
                await session.execute(
                    select(TradeProgram).where(TradeProgram.slug == spec["slug"])
                )
            ).scalars().first()
            if existing is None:
                session.add(TradeProgram(**spec))
                print(f"  + created trade program: {spec['slug']}")
            else:
                for field in _FIELDS:
                    setattr(existing, field, spec[field])
                print(f"  ~ updated trade program: {spec['slug']}")
        await session.commit()
    print(f"\nSeeded {len(PROGRAMS)} trade program(s).")


if __name__ == "__main__":
    asyncio.run(seed_trade_programs())
