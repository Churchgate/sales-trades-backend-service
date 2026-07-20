"""One-off ICP (company-fit) scoring for existing leads, via OpenRouter.

Depends on scripts/enrich_leads.py having already run — this reads Apollo
firmographic data from `Lead.responses['enrichment']` and scores each lead's
company against WTC Abuja's ICP (see app/services/icp_scoring.py for the
rubric and the rationale for how it was adapted from the retired n8n
prospecting pipeline).

Cost control, two ways:
  - Leads with NO enrichment data (free-email domain, or Apollo found nothing
    for their domain) skip the LLM call entirely — the rubric's own anchor
    points score "no company data available" as 0 on every dimension, so
    asking the model to guess from nothing would be pure waste. These get a
    deterministic Skip-tier result instead.
  - Leads sharing a company domain make ONE API call, applied to the whole
    group — industry/financial-capacity/footprint are company-level facts,
    identical for two contacts at the same firm (same optimization
    enrich_leads.py uses).

Idempotent / resumable: a lead already carrying `icp_score` is skipped, so a
failed or interrupted run can just be re-run.

Usage (run from backend/, against prod via Railway):

    # preview only — shows what WOULD be scored/skipped, touches nothing:
    railway run uv run python scripts/score_leads_icp.py

    # actually call OpenRouter and write to the database:
    railway run uv run python scripts/score_leads_icp.py --commit

    # smaller batch while validating cost/quality:
    railway run uv run python scripts/score_leads_icp.py --commit --limit 20
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.database import session_scope  # noqa: E402
from app.core.logging import get_logger  # noqa: E402
from app.models.lead import Lead  # noqa: E402
from app.services import icp_scoring  # noqa: E402
from app.services.icp_scoring import DEFAULT_MODEL, ICPResult  # noqa: E402

logger = get_logger(__name__)

_NO_DATA_RESULT = ICPResult(
    industry_score=0,
    financial_capacity_score=0,
    footprint_score=0,
    trigger_score=0,
    icp_score=0,
    lead_tier="Skip",
    trigger_event="None identified",
    rationale="No company data available (personal/free email address, or the "
    "domain returned nothing from Apollo) — scored per the rubric's own "
    "'no company data available' anchor rather than guessing.",
)


def _apply_result(lead: Lead, result: ICPResult) -> None:
    lead.icp_score = result.icp_score
    lead.icp_tier = result.lead_tier
    lead.icp_rationale = result.rationale


async def run(commit: bool, limit: int | None, model: str) -> None:
    settings = get_settings()
    if commit and not settings.openrouter_api_key:
        print("OPENROUTER_API_KEY is not set — cannot --commit. Aborting.")
        return

    async with session_scope() as session:
        stmt = select(Lead).order_by(Lead.id)
        if limit:
            stmt = stmt.limit(limit)
        leads = list((await session.execute(stmt)).scalars().all())

        already_done = sum(1 for lead in leads if lead.icp_score is not None)
        pending = [lead for lead in leads if lead.icp_score is None]

        no_data: list[Lead] = []
        by_domain: dict[str, list[Lead]] = {}
        for lead in pending:
            enrichment = (lead.responses or {}).get("enrichment")
            if not enrichment:
                no_data.append(lead)
                continue
            domain = enrichment.get("domain") or lead.email.rsplit("@", 1)[-1]
            by_domain.setdefault(domain, []).append(lead)

        print(f"Total leads: {len(leads)}")
        print(f"Already scored (skipped, idempotent): {already_done}")
        print(f"No enrichment data (deterministic Skip, no LLM call): {len(no_data)}")
        print(f"Enriched leads pending LLM scoring: {sum(len(v) for v in by_domain.values())}")
        print(f"Unique companies to call ({model}): {len(by_domain)}\n")

        if not commit:
            print("Sample of companies that would be scored (first 10):")
            for domain, group in list(by_domain.items())[:10]:
                names = ", ".join(f"{ld.first_name} {ld.last_name}" for ld in group[:2])
                more = f" (+{len(group) - 2} more)" if len(group) > 2 else ""
                print(f"  {domain:28} -> {len(group):2} lead(s): {names}{more}")
            print("\nDRY RUN — nothing written, no API calls made. Re-run with --commit.")
            return

        for lead in no_data:
            _apply_result(lead, _NO_DATA_RESULT)
            session.add(lead)
        if no_data:
            await session.commit()

        scored = failed = 0
        tier_counts: dict[str, int] = {}
        async with httpx.AsyncClient() as client:
            for i, (domain, group) in enumerate(by_domain.items(), start=1):
                try:
                    result = await icp_scoring.score_lead(
                        client, settings.openrouter_api_key, group[0], model=model
                    )
                except Exception as exc:  # noqa: BLE001 — log, skip this company, keep going
                    print(f"  ! {domain}: {exc}")
                    failed += len(group)
                    continue

                for lead in group:
                    _apply_result(lead, result)
                    session.add(lead)
                await session.commit()
                scored += len(group)
                tier_counts[result.lead_tier] = tier_counts.get(result.lead_tier, 0) + len(group)

                if i % 20 == 0:
                    print(f"  ... {i}/{len(by_domain)} companies processed")

        for lead in no_data:
            tier_counts[lead.icp_tier] = tier_counts.get(lead.icp_tier, 0) + 1

        print(f"\nDone. scored={scored + len(no_data)} failed={failed}")
        print("Tier distribution:")
        for tier, n in sorted(tier_counts.items()):
            print(f"  {tier:8} {n}")
        print("\nRe-run any time — already-scored leads are skipped.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--commit", action="store_true", help="Call OpenRouter and write (default: dry run)"
    )
    parser.add_argument(
        "--limit", type=int, default=None, help="Cap the number of leads considered"
    )
    parser.add_argument(
        "--model", default=DEFAULT_MODEL, help=f"OpenRouter model id (default: {DEFAULT_MODEL})"
    )
    args = parser.parse_args()
    asyncio.run(run(args.commit, args.limit, args.model))


if __name__ == "__main__":
    main()
