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
    # Trimmed to the three documents we hand out for now: Brochure + both
    # floorplans. Security/Clubhouse/Location are held back until their assets are
    # finalised (Location Overview currently 400s) — restore here + re-seed to bring
    # them back. The stand app reads this list, so these are also the app's options.
    "materials": [
        "Corporate Prospectus",
        "Office Floorplates",
        "Residence Floorplans",
    ],
    "timing_options": ["Immediate", "0-3 months", "3-6 months", "6-12 months", "Future"],
    # Digital-pack delivery (services/pack_delivery.py): each material label maps to
    # the download link(s) emailed to a visitor who requested it — a list, since one
    # material can be more than one file (e.g. floorplates as separate images).
    # Keyed by the exact `materials` labels above; a label with no entry is
    # captured/tagged but not emailed until one is added here.
    #
    # Hosted in Supabase Storage, bucket `campaign-assets` (public). Uses the same
    # single combined "…-Email.pdf" documents as the website (the earlier per-image
    # PNG/WEBP floorplate/floorplan files 400 now). Clubhouse Overview has no file.
    "materials_assets": {
        "Corporate Prospectus": [_BROCHURE_URL],
        "Office Floorplates": [
            "https://uxnddcxhzcjcldpheudk.supabase.co/storage/v1/object/public/campaign-assets/WTC-Abuja-Office-Floorplate-Email.pdf",
        ],
        "Residence Floorplans": [
            "https://uxnddcxhzcjcldpheudk.supabase.co/storage/v1/object/public/campaign-assets/WTC-Abuja-Residential-Floorplans-Email.pdf",
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
    # Trimmed to Brochure + both floorplans (matches the NOG pack). infrastructure_specs
    # (Security) and availability are held back for now — restore here + re-seed to bring
    # them back.
    "materials": [
        "brochure",
        "office_floorplans",
        "residential_plans",
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
    },
}

# Export Launchpad Boot Camp — 2026 first cohort. Cohort-scoped campaign (same
# shape as _NOG_2026_CONFIG above: one campaign per cohort/event, not a shared
# multi-programme bucket) so this cohort's applications are cleanly separable
# from any future cohort's. No tag_map needed — every lead captured under this
# campaign is by definition an Export Launchpad application. No materials/pack:
# applications don't request documents, so pack_delivery_status stays
# not_requested; the applicant instead gets application_confirmation below.
_EXPORT_LAUNCHPAD_2026_CONFIG: dict = {
    "base_tags": ["Export Launchpad", "2026 First Cohort"],
    # Applications are low-volume/high-intent — notify the team per lead (needs
    # CAMPAIGN_NOTIFICATION_EMAIL set; no-ops otherwise).
    "lead_notification": True,
    # Sent once, on first capture, to the applicant (campaign_mailer.py
    # build_application_confirmation_email/send_application_confirmation).
    # from_email must be a verified Sender Identity (or under an authenticated
    # domain) in the WTC_SENDGRID account or sends 403 — confirm Tradeservices@
    # wtcabuja.com is verified there before this goes live; falls back to
    # EVENT_MAIL_FROM_EMAIL/MAIL_FROM_EMAIL otherwise.
    "application_confirmation": {
        "subject": "Your WTC Abuja Application Has Been Received",
        "programme_name": "the Export Launchpad Bootcamp",
        "from_email": "Tradeservices@wtcabuja.com",
        "from_name": "WTC Abuja Trade Services",
        "eligibility": [
            "Valid CAC business registration",
            "A product or service currently sold in the Nigerian market",
            "Clear intent to begin exporting within the next 6–12 months",
        ],
        "contact_email": "Tradeservices@wtcabuja.com",
        "contact_phone": "09164793000",
        "response_days": 3,
        "slot_limit": 20,
        "hero_url": _EMAIL_HERO,
        "logo_url": _EMAIL_LOGO,
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
    {
        "slug": "export-launchpad-2026",
        "name": "Export Launchpad Boot Camp 2026 — First Cohort",
        "status": STATUS_ACTIVE,
        "starts_on": date(2026, 7, 23),
        "ends_on": date(2026, 8, 20),  # cohort date — not "always on" like the website campaign
        "timezone": "Africa/Lagos",
        "config": _EXPORT_LAUNCHPAD_2026_CONFIG,
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
