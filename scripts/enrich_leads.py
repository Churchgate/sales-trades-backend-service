"""One-off Apollo firmographic enrichment for existing leads (all campaigns).

Apollo enriches by company **domain**, not name, so the domain is derived from
each lead's email (skipping free providers — gmail, yahoo, etc., which tell us
nothing about the employer). Enrichment is written to
`Lead.responses['enrichment']` (never overwrites the rest of `responses`) so
the future ICP-scoring layer (scripts/score_leads_icp.py) has structured
firmographic data to score against.

Idempotent / resumable: a lead already carrying `responses['enrichment']` is
skipped, so a failed or interrupted run can just be re-run. Domains are cached
in-memory for the run (several leads commonly share one employer domain), and
persisted across leads immediately so a crash never loses completed work.

Usage (run from backend/, against prod via Railway):

    # preview only — shows what WOULD be queried/written, touches nothing:
    railway run uv run python scripts/enrich_leads.py

    # actually call Apollo and write to the database:
    railway run uv run python scripts/enrich_leads.py --commit

    # smaller batch while validating (e.g. against the ~187 high-intent leads):
    railway run uv run python scripts/enrich_leads.py --commit --limit 20
"""

import argparse
import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402
from sqlalchemy import select  # noqa: E402
from tenacity import (  # noqa: E402
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.core.config import get_settings  # noqa: E402
from app.core.database import session_scope  # noqa: E402
from app.core.logging import get_logger  # noqa: E402
from app.models.lead import Lead  # noqa: E402

logger = get_logger(__name__)

APOLLO_URL = "https://api.apollo.io/api/v1/organizations/enrich"

# Domains that identify a free/personal mailbox, not an employer — enriching
# these would return nothing useful (or enrich the wrong company entirely).
_FREE_EMAIL_DOMAINS = {
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "icloud.com",
    "aol.com", "yahoo.co.uk", "live.com", "protonmail.com", "ymail.com",
    "googlemail.com", "msn.com",
}

# Fields worth keeping from Apollo's response — everything else in that huge
# payload (tech stack, funding events, org chart, ...) isn't needed for scoring.
_KEPT_FIELDS = (
    "estimated_num_employees",
    "annual_revenue",
    "annual_revenue_printed",
    "organization_revenue_printed",
    "industry",
    "industries",
    "keywords",
    "country",
    "city",
    "linkedin_url",
    "short_description",
    "founded_year",
    "publicly_traded_symbol",
)


def _domain_from_email(email: str) -> str | None:
    domain = email.rsplit("@", 1)[-1].strip().lower()
    return None if not domain or domain in _FREE_EMAIL_DOMAINS else domain


class RetryableApolloError(Exception):
    """Rate-limited or transient — worth a retry. Auth/quota errors are not."""


@retry(
    retry=retry_if_exception_type(RetryableApolloError),
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    reraise=True,
)
async def _enrich_domain(client: httpx.AsyncClient, api_key: str, domain: str) -> dict | None:
    """Returns the trimmed org dict, or None if Apollo has nothing for this domain."""
    resp = await client.get(
        APOLLO_URL,
        params={"domain": domain},
        headers={"x-api-key": api_key, "accept": "application/json"},
    )
    if resp.status_code == 429:
        raise RetryableApolloError(f"rate limited on {domain}")
    if resp.status_code in (401, 403):
        raise RuntimeError(
            f"Apollo auth/quota error ({resp.status_code}) on {domain}: {resp.text[:200]} "
            "— stopping the batch rather than burning the rest of the quota on failures."
        )
    if resp.status_code >= 500:
        raise RetryableApolloError(f"Apollo {resp.status_code} on {domain}")
    resp.raise_for_status()
    org = resp.json().get("organization")
    if not org:
        return None
    return {k: org.get(k) for k in _KEPT_FIELDS if org.get(k) is not None}


async def run(commit: bool, limit: int | None) -> None:
    settings = get_settings()
    if commit and not settings.apollo_api_key:
        print("APOLLO_API_KEY is not set — cannot --commit. Aborting.")
        return

    async with session_scope() as session:
        stmt = select(Lead).order_by(Lead.id)
        if limit:
            stmt = stmt.limit(limit)
        leads = list((await session.execute(stmt)).scalars().all())

        already_done = sum(1 for lead in leads if "enrichment" in (lead.responses or {}))
        pending = [lead for lead in leads if "enrichment" not in (lead.responses or {})]

        by_domain: dict[str, list[Lead]] = {}
        skipped_free = 0
        skipped_no_email = 0
        for lead in pending:
            if not lead.email:
                skipped_no_email += 1
                continue
            domain = _domain_from_email(lead.email)
            if domain is None:
                skipped_free += 1
                continue
            by_domain.setdefault(domain, []).append(lead)

        print(f"Total leads: {len(leads)}")
        print(f"Already enriched (skipped, idempotent): {already_done}")
        print(f"Free-email / no-email (not enrichable): {skipped_free + skipped_no_email}")
        print(f"Corporate-domain leads pending: {sum(len(v) for v in by_domain.values())}")
        print(f"Unique domains to query: {len(by_domain)}\n")

        if not commit:
            print("Sample of domains that would be queried (first 15):")
            for domain, group in list(by_domain.items())[:15]:
                names = ", ".join(f"{ld.first_name} {ld.last_name}" for ld in group[:3])
                more = f" (+{len(group) - 3} more)" if len(group) > 3 else ""
                print(f"  {domain:28} -> {len(group):2} lead(s): {names}{more}")
            print("\nDRY RUN — nothing written. Re-run with --commit to call Apollo.")
            return

        found = not_found = failed = 0
        async with httpx.AsyncClient(timeout=30) as client:
            for i, (domain, group) in enumerate(by_domain.items(), start=1):
                try:
                    org = await _enrich_domain(client, settings.apollo_api_key, domain)
                except RuntimeError as exc:
                    print(f"\n! STOPPING: {exc}")
                    break
                except Exception as exc:  # noqa: BLE001 — log, skip this domain, keep going
                    print(f"  ! {domain}: {exc}")
                    failed += len(group)
                    continue

                if org is None:
                    not_found += len(group)
                else:
                    enrichment = {
                        **org,
                        "domain": domain,
                        "enriched_at": datetime.now(UTC).isoformat(),
                    }
                    for lead in group:
                        # Reassign the whole dict — Lead.responses is a plain
                        # dict column (not MutableDict), so in-place mutation
                        # (`lead.responses["enrichment"] = ...`) would not be
                        # detected as a change by SQLAlchemy's session.
                        lead.responses = {**(lead.responses or {}), "enrichment": enrichment}
                        session.add(lead)
                    await session.commit()
                    found += len(group)

                if i % 20 == 0:
                    print(f"  ... {i}/{len(by_domain)} domains processed")

        print(
            f"\nDone. domains_found={found} domains_not_found={not_found} "
            f"domains_failed={failed}\nRe-run any time — already-enriched leads are skipped."
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--commit", action="store_true", help="Call Apollo and write (default: dry run)"
    )
    parser.add_argument(
        "--limit", type=int, default=None, help="Cap the number of leads considered"
    )
    args = parser.parse_args()
    asyncio.run(run(args.commit, args.limit))


if __name__ == "__main__":
    main()
