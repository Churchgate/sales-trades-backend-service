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
    # Internal "New Application" alert, sent to the Trade Services team on
    # every genuinely new registration — the Trade equivalent of the old
    # campaign's config["lead_notification"]=True (which went to the shared
    # settings.campaign_notification_email). Distinct recipient here since
    # Export Launchpad applications should route straight to the team that
    # actually runs the cohort, not the general enquiries inbox.
    "lead_notification": {
        "enabled": True,
        "to_email": "Tradeservices@wtcabuja.com",
    },
    "company_founded_options": [
        "Less than 2 years", "2-5 years", "5-9 years", "10-20 years", "More than 20 years",
    ],
    "topics_of_interest_options": [
        "E-Commerce", "ESG", "Finance and Tax", "Managing a Global Workforce",
        "Tariff Playbook", "Supply Chain", "Export Bootcamp", "Other",
    ],
    # Live in production (flipped on once the integration was validated
    # end-to-end and the dedicated Freshsales lead source was wired in — see
    # trade_crm_sync.py). This script is idempotent and re-applies `config`
    # in full on every run, so this MUST track the real production value,
    # not a "still testing" placeholder — verified live via `railway run`
    # against the production DB before changing this back to True here.
    "crm_sync_enabled": True,
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
        # "Export LP EH.png" has "EXPORT LAUNCHPAD BOOT CAMP" baked into the
        # image itself — trade_mailer.build_application_confirmation_email
        # skips its own heading-text overlay whenever hero_url is set, so
        # this photo's own text is the only thing shown (no collision).
        "hero_url": (
            "https://uxnddcxhzcjcldpheudk.supabase.co/storage/v1/object/public/"
            "campaign-assets/Export%20LP%20EH.png"
        ),
        "logo_url": _EMAIL_LOGO,
    },
}

# --- Trade Mission programs (Canton Fair, MUSIAD Expo, Belgium-Luxembourg) ---
# Replace three Google Forms that previously collected these applications.
# Unlike Export Launchpad, mission-specific fields (passport info, visa
# status, travel history, ticket tier, sector of activities) are NOT
# promoted to typed TradeLead columns — they live in `responses` only (see
# TradeLeadOut.responses), since a new typed column per mission would bloat
# the schema indefinitely as more missions are added.
_MISSION_REQUIRED_DOCUMENTS = [
    {"key": "passport_data_page", "label": "Passport Data Page", "required": True},
]

# New — no admin approval concept exists in Trade before this change. Both
# `require_admin_approval: True` and `crm_sync_enabled: True` are safe to set
# together from day one: nothing reaches Freshsales until an admin approves a
# participant regardless of the sync kill switch, so there's no need for a
# separate "test mode" flag layered on top (see trade_crm_sync.py).
_MISSION_BASE_CONFIG = {
    "required_documents": _MISSION_REQUIRED_DOCUMENTS,
    "require_admin_approval": True,
    "crm_sync_enabled": True,
    # Dedicated Freshsales lead source not created yet (Admin > Sales Force
    # Automation > Sources, same manual precedent as "Export Launch
    # Pad-Cohort 1" / "NOG-Week-2026") — falls back to the Export Launchpad
    # source until set. Non-blocking: require_admin_approval already gates
    # everything, so this can be filled in anytime before the first approval.
    "crm_lead_source_id": None,
}

_CANTON_FAIR_CONFIG = {
    **_MISSION_BASE_CONFIG,
    "base_tags": ["Canton Fair", "Trade Mission", "canton-fair-2026"],
    "lead_notification": {"enabled": True, "to_email": "Tradeservices@wtcabuja.com"},
    "application_confirmation": {
        "subject": "Your WTC Abuja Canton Fair Application Has Been Received",
        "programme_name": "the 140th Canton Fair Trade Mission to Guangzhou, China",
        "from_email": "Tradeservices@wtcabuja.com",
        "from_name": "WTC Abuja Trade Services",
        "contact_email": "Tradeservices@wtcabuja.com",
        "contact_phone": "09164793000",
        "response_days": 3,
        "logo_url": _EMAIL_LOGO,
    },
}

_MUSIAD_EXPO_CONFIG = {
    **_MISSION_BASE_CONFIG,
    "base_tags": ["MUSIAD Expo", "Trade Mission", "musiad-expo-2026"],
    "lead_notification": {"enabled": True, "to_email": "Tradeservices@wtcabuja.com"},
    "application_confirmation": {
        "subject": "Your WTC Abuja MUSIAD Expo Application Has Been Received",
        "programme_name": "the MUSIAD Expo 2026 Trade Mission to Istanbul, Türkiye",
        "from_email": "Tradeservices@wtcabuja.com",
        "from_name": "WTC Abuja Trade Services",
        "contact_email": "Tradeservices@wtcabuja.com",
        "contact_phone": "09164793000",
        "response_days": 3,
        "logo_url": _EMAIL_LOGO,
    },
}

_BELGIUM_LUXEMBOURG_CONFIG = {
    **_MISSION_BASE_CONFIG,
    "base_tags": ["Belgium-Luxembourg CBL", "Trade Mission", "belgium-luxembourg-2026"],
    "lead_notification": {"enabled": True, "to_email": "Tradeservices@wtcabuja.com"},
    "application_confirmation": {
        "subject": "Your WTC Abuja Nigeria-Belgium-Luxembourg Business Forum Application "
        "Has Been Received",
        "programme_name": "the 4th High-Level Nigeria–Belgium–Luxembourg Business Forum "
        "in Brussels, Belgium",
        "from_email": "Tradeservices@wtcabuja.com",
        "from_name": "WTC Abuja Trade Services",
        "contact_email": "Tradeservices@wtcabuja.com",
        "contact_phone": "09164793000",
        "response_days": 3,
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
    {
        "slug": "canton-fair-2026",
        "name": "140th Canton Fair Trade Mission — Guangzhou, China",
        "kind": KIND_BOOT_CAMP,
        "status": STATUS_ACTIVE,
        "starts_on": date(2026, 10, 15),
        "ends_on": date(2026, 11, 4),
        "timezone": "Africa/Lagos",
        "config": _CANTON_FAIR_CONFIG,
    },
    {
        "slug": "musiad-expo-2026",
        "name": "MUSIAD Expo 2026 — Istanbul, Türkiye",
        "kind": KIND_BOOT_CAMP,
        "status": STATUS_ACTIVE,
        "starts_on": date(2026, 9, 23),
        "ends_on": date(2026, 9, 26),
        "timezone": "Africa/Lagos",
        "config": _MUSIAD_EXPO_CONFIG,
    },
    {
        "slug": "belgium-luxembourg-2026",
        "name": "4th High-Level Nigeria–Belgium–Luxembourg Business Forum (CBL 2026)",
        "kind": KIND_BOOT_CAMP,
        "status": STATUS_ACTIVE,
        "starts_on": date(2026, 10, 28),
        "ends_on": date(2026, 10, 30),
        "timezone": "Africa/Lagos",
        "config": _BELGIUM_LUXEMBOURG_CONFIG,
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
