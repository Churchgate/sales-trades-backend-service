"""Seed (or update) lead-capture campaigns. There's no admin UI for creation yet,
so campaigns live here.

Idempotent: matches on slug, updates details, leaves captured leads untouched.
Each campaign's `config` drives the booth app's dynamic form/content, so a new
event is just a new entry in CAMPAIGNS below. Run:

    uv run python scripts/seed_campaigns.py
"""

import asyncio
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.core.database import session_scope
from app.models.campaign import STATUS_ACTIVE, Campaign

# WTC Abuja Interactive Stand App (boot-app brief). Interests/materials/tags map
# to brief §12 (routes), §18 (conversion surface), §19 (timing), §20 (consent),
# §22 (CRM tags).
_NOG_2026_CONFIG: dict = {
    "interests": [
        "Office Leasing",
        "Executive Residences",
        "Corporate Accommodation",
        "Security & Continuity",
        "Clubhouse",
        "Location",
    ],
    "materials": [
        "Corporate Prospectus",
        "Office Floorplates",
        "Residence Floorplans",
        "Security & Continuity Brief",
        "Clubhouse Overview",
        "Location Overview",
    ],
    "timing_options": ["Immediate", "0-3 months", "3-6 months", "6-12 months", "Future"],
    # Digital-pack delivery (services/pack_delivery.py): each material label maps to
    # the download link emailed to a visitor who requested it. Keyed by the exact
    # `materials` labels above. REPLACE these placeholders with the real hosted
    # asset URLs before the campaign goes live (2026-07-05) — anything without a
    # link here is captured/tagged but not emailed.
    "materials_assets": {
        "Corporate Prospectus": "https://assets.wtcabuja.com/nog-2026/corporate-prospectus.pdf",
        "Office Floorplates": "https://assets.wtcabuja.com/nog-2026/office-floorplates.pdf",
        "Residence Floorplans": "https://assets.wtcabuja.com/nog-2026/residence-floorplans.pdf",
        "Security & Continuity Brief": "https://assets.wtcabuja.com/nog-2026/security-continuity-brief.pdf",
        "Clubhouse Overview": "https://assets.wtcabuja.com/nog-2026/clubhouse-overview.pdf",
        "Location Overview": "https://assets.wtcabuja.com/nog-2026/location-overview.pdf",
    },
    # Optional copy override for the digital-pack email (defaults live in the service).
    "digital_pack": {
        "subject": "Your WTC Abuja digital pack",
        "intro": (
            "Thank you for visiting the World Trade Center Abuja stand. As "
            "requested, here are your materials to download:"
        ),
    },
    "consent": {
        "required": (
            "By submitting this form, you agree that World Trade Center Abuja may "
            "contact you about your selected enquiry and send the requested materials."
        ),
        "marketing": (
            "I would also like to receive WTC Abuja updates, availability news, private "
            "invitations and marketing communications. I can opt out at any time."
        ),
    },
    # CRM tagging (brief §22) — base tags applied to every lead, plus per-flag tags.
    "base_tags": ["Stand App", "NOG Energy Week 2026"],
    "tag_map": {
        "Office Leasing": "Office Leasing",
        "Executive Residences": "Executive Residences",
        "Corporate Accommodation": "Corporate Accommodation",
        "Security & Continuity": "Security & Continuity",
        "Clubhouse": "Clubhouse",
        "Location": "Location",
    },
    "digital_pack_tag": "Digital Pack",
    "inspection_tag": "Private Inspection",
    "newsletter_tag": "Newsletter Opt-In",
}

CAMPAIGNS: list[dict] = [
    {
        "slug": "nog-2026",
        "name": "NOG Energy Week 2026 — WTC Abuja Stand",
        "status": STATUS_ACTIVE,
        "starts_on": date(2026, 7, 5),
        "ends_on": date(2026, 7, 9),
        "timezone": "Africa/Lagos",
        "config": _NOG_2026_CONFIG,
    },
]

_FIELDS = ("name", "status", "starts_on", "ends_on", "timezone", "config")


async def seed_campaigns() -> None:
    async with session_scope() as session:
        for spec in CAMPAIGNS:
            existing = (
                await session.execute(select(Campaign).where(Campaign.slug == spec["slug"]))
            ).scalars().first()
            if existing is None:
                session.add(Campaign(**spec))
                print(f"  + created campaign: {spec['slug']}")
            else:
                for field in _FIELDS:
                    setattr(existing, field, spec[field])
                print(f"  ~ updated campaign: {spec['slug']}")
        await session.commit()
    print(f"\nSeeded {len(CAMPAIGNS)} campaigns.")


if __name__ == "__main__":
    asyncio.run(seed_campaigns())
