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
    # the download link(s) emailed to a visitor who requested it — a list, since one
    # material can be more than one file (e.g. floorplates as separate images).
    # Keyed by the exact `materials` labels above; a label with no entry is
    # captured/tagged but not emailed until one is added here.
    #
    # Hosted in Supabase Storage, bucket `campaign-assets` (public). Still
    # missing real files for Corporate Prospectus, Security & Continuity Brief,
    # Clubhouse Overview — add their entries here as those land from other
    # departments, then re-run this script.
    "materials_assets": {
        "Office Floorplates": [
            "https://uxnddcxhzcjcldpheudk.supabase.co/storage/v1/object/public/campaign-assets/corporate-office_floorplate1.png",
            "https://uxnddcxhzcjcldpheudk.supabase.co/storage/v1/object/public/campaign-assets/corporate-office_floorplate2.webp",
        ],
        "Residence Floorplans": [
            "https://uxnddcxhzcjcldpheudk.supabase.co/storage/v1/object/public/campaign-assets/residences-1br.png",
            "https://uxnddcxhzcjcldpheudk.supabase.co/storage/v1/object/public/campaign-assets/residences-2br.png",
            "https://uxnddcxhzcjcldpheudk.supabase.co/storage/v1/object/public/campaign-assets/residences-3br.png",
        ],
        "Location Overview": [
            "https://uxnddcxhzcjcldpheudk.supabase.co/storage/v1/object/public/campaign-assets/location_overview.png",
        ],
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
