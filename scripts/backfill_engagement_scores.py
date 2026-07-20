"""One-off backfill of `Lead.engagement_score` for leads captured before the
persisted-score column existed (migration 755cca72bb41).

Going forward, `engagement_score` is kept current by
`services/lead_service.py` (on capture) and `services/email_event_ingest.py`
(on pack open/click) — this script only needs to run once, to backfill
existing rows whose `engagement_score` defaulted to 0 at migration time.

Idempotent: recomputes deterministically from each lead's current fields, so
it's safe to re-run (e.g. after tuning the weights in lead_scoring.py).

Usage (run from backend/, against prod via Railway):

    # preview only — shows the score distribution that WOULD be written:
    railway run uv run python scripts/backfill_engagement_scores.py

    # actually write engagement_score + score_computed_at for every lead:
    railway run uv run python scripts/backfill_engagement_scores.py --commit
"""

import argparse
import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.core.database import session_scope  # noqa: E402
from app.models.lead import Lead  # noqa: E402
from app.services import lead_scoring  # noqa: E402


async def run(commit: bool) -> None:
    async with session_scope() as session:
        leads = list((await session.execute(select(Lead).order_by(Lead.id))).scalars().all())

        buckets = {"0-19": 0, "20-39": 0, "40-59": 0, "60-79": 0, "80-100": 0}
        now = datetime.now(UTC)
        for lead in leads:
            new_score = lead_scoring.engagement_score(lead, now=now)
            bucket = (
                "0-19" if new_score < 20 else
                "20-39" if new_score < 40 else
                "40-59" if new_score < 60 else
                "60-79" if new_score < 80 else
                "80-100"
            )
            buckets[bucket] += 1
            if commit:
                lead.engagement_score = new_score
                lead.score_computed_at = now
                session.add(lead)

        print(f"Total leads: {len(leads)}")
        print("Score distribution:")
        for bucket, n in buckets.items():
            print(f"  {bucket:8} {n}")

        if not commit:
            print("\nDRY RUN — nothing written. Re-run with --commit to persist.")
            return

        await session.commit()
        print(f"\nDone. engagement_score backfilled for {len(leads)} leads.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--commit", action="store_true", help="Write engagement_score (default: dry run)"
    )
    args = parser.parse_args()
    asyncio.run(run(args.commit))


if __name__ == "__main__":
    main()
