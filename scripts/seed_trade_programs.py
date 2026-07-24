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
_EMAIL_LOGO = (
    "https://uxnddcxhzcjcldpheudk.supabase.co/storage/v1/object/public/"
    "campaign-assets/Abuja_WTC-LOGO_HORZ-white.png"
)

_EXPORT_LAUNCHPAD_REQUIRED_DOCUMENTS = [
    {"key": "cac_certificate", "label": "CAC Certificate", "required": True},
    {"key": "logo", "label": "Company Logo", "required": True},
    {"key": "company_profile", "label": "Company Profile / Brochure", "required": False},
    {"key": "business_plan", "label": "Business Plan", "required": False},
]

_EXPORT_LAUNCHPAD_CONFIG = {
    "email_template": "export_launchpad_confirmation",  # distinct from the campaign templates
    "required_documents": _EXPORT_LAUNCHPAD_REQUIRED_DOCUMENTS,
    # Freshsales tags pushed on every synced contact — matches what the
    # original campaign-era registrations already carry live in CRM (verified
    # on Amaka Eze's contact, the one real pre-Trade registration), so the
    # cohort stays under one consistent tag set rather than splitting across
    # an old and a new naming scheme. See services/trade_capture.py.
    "base_tags": ["Export Launchpad", "2026 First Cohort", "export-launchpad"],
    "company_founded_options": [
        "Less than 2 years", "2-5 years", "5-9 years", "10-20 years", "More than 20 years",
    ],
    "topics_of_interest_options": [
        "E-Commerce", "ESG", "Finance and Tax", "Managing a Global Workforce",
        "Tariff Playbook", "Supply Chain", "Export Bootcamp", "Other",
    ],
    # Still testing this program end-to-end (matches the crm_sync_enabled=False
    # stance the export-launchpad-2026 CAMPAIGN was seeded with in
    # scripts/seed_campaigns.py) — don't push registrations into the live
    # Freshsales pipeline yet. Flip to True (or remove) once ready to go live.
    "crm_sync_enabled": False,
    # Sent once, on first capture, to each participant with an email address
    # (services/trade_mailer.py). from_email must be a verified Sender
    # Identity in the WTC_SENDGRID account or sends 403 — confirm
    # Tradeservices@wtcabuja.com is verified there before this goes live;
    # falls back to EVENT_MAIL_FROM_EMAIL/MAIL_FROM_EMAIL otherwise. Copy
    # mirrors the campaign-era config this replaces (scripts/seed_campaigns.py).
    "application_confirmation": {
        "subject": "Your WTC Abuja Application Has Been Received",
        "programme_name": "the Export Launchpad Bootcamp",
        "from_email": "Tradeservices@wtcabuja.com",
        "from_name": "WTC Abuja Trade Services",
        "eligibility": [
            "Valid CAC business registration",
            "A product or service currently sold in the Nigerian market",
            "Clear intent to begin exporting within the next 6-12 months",
        ],
        "contact_email": "Tradeservices@wtcabuja.com",
        "contact_phone": "09164793000",
        "response_days": 3,
        "slot_limit": 20,
        "hero_url": (
            "https://uxnddcxhzcjcldpheudk.supabase.co/storage/v1/object/public/"
            "campaign-assets/Export%20LP%20EH.png"
        ),
        "logo_url": _EMAIL_LOGO,
    },
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
