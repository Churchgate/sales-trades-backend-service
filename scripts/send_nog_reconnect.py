"""One-off post-event "reconnect" broadcast to every NOG 2026 lead.

Sends a short follow-up email (text + one inline hosted image — no attachment)
inviting each visitor to book a tour of WTC Abuja. Goes to every lead in the
`nog-2026` campaign that has a valid email, via the WTC SendGrid account
(enquiries@wtcabuja.com), and is tagged `email_kind="reconnect"` so opens/clicks
attribute back to the lead like the pack/viewing emails.

Safe by construction:
  * dry run by default — prints who WOULD receive it and touches nothing;
  * `--test you@example.com` sends ONE copy to you (using a throwaway lead) so you
    can eyeball the render before the blast — no DB writes, no other recipients;
  * `--commit` performs the real send; each successful send stamps
    `responses["reconnect_sent_at"]`, so a re-run skips anyone already emailed
    (idempotent) unless you pass `--resend`;
  * `--image` is REQUIRED for --test/--commit — email clients only render hosted
    images, so there is nothing to send until the recap image has a public URL.

Usage (run from backend/, against prod via Railway):

    # preview only — no image needed, writes nothing:
    railway run uv run python scripts/send_nog_reconnect.py

    # send one test to yourself first (always do this):
    railway run uv run python scripts/send_nog_reconnect.py \
        --image https://.../campaign-assets/nog-recap.jpg --test you@example.com

    # real broadcast to all ~430 leads:
    railway run uv run python scripts/send_nog_reconnect.py \
        --image https://.../campaign-assets/nog-recap.jpg --commit
"""

import argparse
import asyncio
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import get_settings  # noqa: E402
from app.core.database import session_scope  # noqa: E402
from app.models.lead import Lead  # noqa: E402
from app.repositories import campaigns_repo, leads_repo  # noqa: E402
from app.services import campaign_mailer  # noqa: E402

CAMPAIGN_SLUG = "nog-2026"
SENT_KEY = "reconnect_sent_at"  # responses stamp marking a lead already emailed
SEND_DELAY_S = 0.15  # gentle pacing between sends (well under SendGrid limits)
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _valid_email(email: str | None) -> bool:
    return bool(email and _EMAIL_RE.match(email.strip()))


async def _send_test(
    image_url: str, to_email: str, first_name: str = "Mofe", cc: list[str] | None = None
) -> None:
    settings = get_settings()
    async with session_scope() as session:
        campaign = await campaigns_repo.get_by_slug(session, CAMPAIGN_SLUG)
    if campaign is None:
        print(f"ERROR: campaign '{CAMPAIGN_SLUG}' not found.")
        return
    # A throwaway, un-persisted lead so the template renders exactly as it will for
    # real recipients (first-name greeting + subject). id=None -> no tracking args.
    test_lead = Lead(first_name=first_name, email=to_email, campaign_id=campaign.id)
    subject, _html, _text = campaign_mailer.build_reconnect_email(
        test_lead, campaign, image_url
    )
    cc_note = f"  (cc: {', '.join(cc)})" if cc else ""
    print(f'Sending ONE test to {to_email}{cc_note}\n  subject: "{subject}"')
    ok = await campaign_mailer.send_reconnect(test_lead, campaign, image_url, settings, cc=cc)
    print("  -> sent ✓" if ok else "  -> FAILED (check WTC_SENDGRID_API_KEY / logs)")


async def run(
    image_url: str, *, commit: bool, resend: bool, limit: int | None, cc: list[str] | None
) -> None:
    settings = get_settings()
    async with session_scope() as session:
        campaign = await campaigns_repo.get_by_slug(session, CAMPAIGN_SLUG)
        if campaign is None:
            print(f"ERROR: campaign '{CAMPAIGN_SLUG}' not found.")
            return
        leads = await leads_repo.list_for_campaign(session, campaign.id, limit=100_000)

        eligible: list[Lead] = []
        no_email = already = 0
        for lead in leads:
            if not _valid_email(lead.email):
                no_email += 1
                continue
            if not resend and (lead.responses or {}).get(SENT_KEY):
                already += 1
                continue
            eligible.append(lead)
        if limit is not None:
            eligible = eligible[:limit]

        print("=== NOG 2026 reconnect broadcast ===")
        print(f"  leads in campaign:        {len(leads)}")
        print(f"  skipped (no/bad email):   {no_email}")
        print(f"  skipped (already sent):   {already}{'  [ignored: --resend]' if resend else ''}")
        print(f"  ELIGIBLE this run:        {len(eligible)}")
        print(f"  image: {image_url or '(none — required to send)'}")
        if cc:
            print(f"  cc (per email):           {', '.join(cc)}")
        print()

        if not commit:
            print("Sample of recipients (first 8):")
            for lead in eligible[:8]:
                print(f"  {lead.first_name or '(no name)':16} <{lead.email}>")
            print("\nDRY RUN — nothing sent. Add --image <url> --commit to send.")
            return

        if not image_url:
            print("ERROR: --image <public-url> is required to --commit.")
            return

        sent = failed = 0
        for i, lead in enumerate(eligible, 1):
            ok = await campaign_mailer.send_reconnect(lead, campaign, image_url, settings, cc=cc)
            if ok:
                lead.responses = {**(lead.responses or {}), SENT_KEY: datetime.now(UTC).isoformat()}
                await leads_repo.update(session, lead)
                sent += 1
            else:
                failed += 1
                print(f"  ! FAILED {lead.email}")
            if i % 50 == 0:
                print(f"  ... {i}/{len(eligible)} processed")
            await asyncio.sleep(SEND_DELAY_S)

    print(f"\nDone. sent={sent}  failed={failed}  (of {len(eligible)} eligible)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default="", help="Public URL of the recap image (required to send)")
    parser.add_argument("--test", metavar="EMAIL", help="Send ONE test to this address, then exit")
    parser.add_argument("--test-name", default="Mofe", help="First name used in the --test greeting")
    parser.add_argument("--commit", action="store_true", help="Send the real broadcast (default: dry run)")
    parser.add_argument("--resend", action="store_true", help="Include leads already emailed")
    parser.add_argument("--limit", type=int, help="Cap the number of recipients (safety/testing)")
    parser.add_argument(
        "--cc", action="append", metavar="EMAIL",
        help="CC each email to this address (repeatable). One copy per recipient.",
    )
    args = parser.parse_args()

    if args.test:
        if not args.image:
            parser.error("--test requires --image <public-url>")
        asyncio.run(_send_test(args.image, args.test, args.test_name, cc=args.cc))
    else:
        asyncio.run(run(
            args.image, commit=args.commit, resend=args.resend, limit=args.limit, cc=args.cc
        ))


if __name__ == "__main__":
    main()
