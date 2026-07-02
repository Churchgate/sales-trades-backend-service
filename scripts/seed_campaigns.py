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

# Shared transactional-email assets (Supabase campaign-assets bucket). The hero is
# pre-darkened and the logo pre-whitened so white overlay text is legible in every
# client (email clients strip CSS scrims / invert filters) — see campaign_mailer.
_A = "https://uxnddcxhzcjcldpheudk.supabase.co/storage/v1/object/public/campaign-assets"
_EMAIL_HERO = f"{_A}/wtc-bg-hero.jpg"
_EMAIL_LOGO = f"{_A}/Abuja_WTC-LOGO_HORZ-white.png"
_BROCHURE_URL = f"{_A}/WTC-Abuja-Brochure.pdf"

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
    # missing real files for Corporate Prospectus, Clubhouse Overview — add
    # their entries here as those land from other departments, then re-run
    # this script.
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
        "Security & Continuity Brief": [
            "https://uxnddcxhzcjcldpheudk.supabase.co/storage/v1/object/public/campaign-assets/WTC%20Abuja%20Security%20Presentation%202026.pdf",
        ],
    },
    # Digital-pack + viewing-confirmation email copy/branding (services/campaign_mailer).
    # event_line is NOG-only — website leads omit it (source-aware footer).
    "digital_pack": {
        "subject": "Your WTC Abuja Digital Pack",
        "intro": (
            "Thank you for visiting the World Trade Center Abuja stand at NOG "
            "Energy Week. As requested, your materials are below — tap any button "
            "to download."
        ),
        "event_line": "NOG Energy Week 2026 &middot; 5–9 July 2026",
        "hero_url": _EMAIL_HERO,
        "logo_url": _EMAIL_LOGO,
    },
    "viewing_booking": {
        "subject": "Your WTC Abuja Viewing Request",
        "intro": (
            "Thank you for requesting a viewing at the World Trade Center Abuja "
            "stand. A member of our team will be in touch shortly to confirm the "
            "details and arrange a time that works for you."
        ),
        "brochure_url": _BROCHURE_URL,
    },
    # Per-material display copy for the pack email's download rows (keyed by the
    # `materials` labels above). `featured` renders the highlighted panel style.
    "materials_display": {
        "Corporate Prospectus": {
            "eyebrow": "Full Brochure",
            "title": "Full Development Brochure",
            "description": "Offices, residences, clubhouse and infrastructure.",
            "featured": True,
        },
        "Office Floorplates": {
            "eyebrow": "Office Floorplate",
            "title": "Grade A Offices",
            "description": "1,440 m² typical floor · Shell & core to Cat B fit-out",
        },
        "Residence Floorplans": {
            "eyebrow": "Residence Floorplans",
            "title": "Executive Residences",
            "description": "1-bed, 2-bed & 3-bed apartments · Penthouses on request",
        },
        "Security & Continuity Brief": {
            "eyebrow": "Security & Continuity",
            "title": "Security & Continuity Brief",
            "description": "Perimeter, access control and business-continuity overview.",
        },
        "Location Overview": {
            "eyebrow": "Location",
            "title": "Location Overview",
            "description": "Central Business District, Abuja.",
        },
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

# WTC Abuja public website (https://wtcabuja.com) enquiry form. Same event shape
# as the stand app, but the website form submits machine slugs for its materials
# (brochure, office_floorplans, …) rather than the human labels the stand app
# uses — so this campaign's `materials`/`materials_assets` are keyed by those
# slugs, or nothing would be deliverable (deliverable_materials matches labels
# exactly). `availability` is a known material with no asset yet: leads requesting
# only it are captured/tagged but not emailed until a file is added here (then a
# resend/retry delivers). The other four deliver today.
#
# The website also submits interest slugs (offices/residences/both/security/full/
# investment/general) — mapped to CRM tags via the tag_map override below. The
# `interests` list inherited from the stand app is unused server-side for the
# website (its form is hardcoded, not driven by this config), so it's left as-is.
_WTC_WEBSITE_CONFIG: dict = {
    **_NOG_2026_CONFIG,
    "materials": [
        "brochure",
        "office_floorplans",
        "residential_plans",
        "infrastructure_specs",
        "availability",
    ],
    "materials_assets": {
        "brochure": [
            "https://uxnddcxhzcjcldpheudk.supabase.co/storage/v1/object/public/campaign-assets/WTC-Abuja-Brochure.pdf",
        ],
        # Website uses the single combined "…-Email.pdf" documents (one clean
        # download per row), not the stand app's multi-image sets.
        "office_floorplans": [
            "https://uxnddcxhzcjcldpheudk.supabase.co/storage/v1/object/public/campaign-assets/WTC-Abuja-Office-Floorplate-Email.pdf",
        ],
        "residential_plans": [
            "https://uxnddcxhzcjcldpheudk.supabase.co/storage/v1/object/public/campaign-assets/WTC-Abuja-Residential-Floorplans-Email.pdf",
        ],
        "infrastructure_specs": [
            "https://uxnddcxhzcjcldpheudk.supabase.co/storage/v1/object/public/campaign-assets/WTC%20Abuja%20Security%20Presentation%202026.pdf",
        ],
    },
    # Website leads are a distinct channel, not the NOG stand — so drop the stand's
    # base tags for a website-specific pair.
    "base_tags": ["WTC Abuja", "Website"],
    # The website's two forms submit these interest slugs (index.html #finterest /
    # #pinterest). _derive_tags passes any unmapped interest through as its raw
    # slug, so without this every website lead was tagged e.g. "residences". Keys =
    # the exact form option values; values reuse the stand app's tag names where the
    # category matches (offices/residences/security) so both channels group together
    # in the CRM, with website-only options tagged in their own words.
    "tag_map": {
        "offices": "Office Leasing",
        "residences": "Executive Residences",
        "both": "Office & Residences",
        "security": "Security & Continuity",
        "full": "Full Development Tour",
        "investment": "Investment / Purchase",
        "general": "General Information",
    },
    # Source-aware email copy: website leads didn't visit a stand, so the intro is
    # neutral and the NOG event_line is dropped (inherited digital_pack replaced).
    "digital_pack": {
        "subject": "Your WTC Abuja Digital Pack",
        "intro": (
            "Thank you for your interest in World Trade Center Abuja. As requested, "
            "your materials are below — tap any button to download."
        ),
        "hero_url": _EMAIL_HERO,
        "logo_url": _EMAIL_LOGO,
    },
    "viewing_booking": {
        "subject": "Your WTC Abuja Viewing Request",
        "intro": (
            "Thank you for requesting a viewing at World Trade Center Abuja. A "
            "member of our team will be in touch shortly to confirm the details "
            "and arrange a time that works for you."
        ),
        "brochure_url": _BROCHURE_URL,
    },
    # Keyed by the website's material slugs (see `materials` above).
    "materials_display": {
        "brochure": {
            "eyebrow": "Full Brochure",
            "title": "Full Development Brochure",
            "description": "Offices, residences, clubhouse and infrastructure.",
            "featured": True,
        },
        "office_floorplans": {
            "eyebrow": "Office Floorplate",
            "title": "Grade A Offices",
            "description": "1,440 m² typical floor · Shell & core to Cat B fit-out",
        },
        "residential_plans": {
            "eyebrow": "Residence Floorplans",
            "title": "Executive Residences",
            "description": "1-bed, 2-bed & 3-bed apartments · Penthouses on request",
        },
        "infrastructure_specs": {
            "eyebrow": "Security & Continuity",
            "title": "Security & Continuity Brief",
            "description": "Perimeter, access control and business-continuity overview.",
        },
    },
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
    {
        "slug": "wtcabuja-website",
        "name": "WTC Abuja Website",
        "status": STATUS_ACTIVE,
        "starts_on": date(2026, 7, 5),
        "ends_on": date(2056, 7, 9),  # "always on" — the public site has no end date
        "timezone": "Africa/Lagos",
        "config": _WTC_WEBSITE_CONFIG,
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
