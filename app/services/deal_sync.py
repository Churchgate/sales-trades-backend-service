"""Scheduled deal-view sync (spec §6B): reconciliation + `rotten_days` refresh.

For each active pipeline, paginate `/api/deals/view/{view_id}` fully and upsert
`deals_snapshot`. This catches anything webhooks missed and refreshes fields
the webhook payload doesn't carry (`rotten_days`, `age`).

Two open items, both non-blocking for the foundation build:

- `view_id` is a Freshsales "saved view" id, distinct from the pipeline id and
  not returned by any reference endpoint in scope (spec §5). It must be
  configured manually on `pipelines.view_id`; pipelines without one are
  skipped here with a warning.
- The exact field names on a `/api/deals/view/{view_id}` record haven't been
  validated against a live response (no API key configured yet). `_parse_view_deal`
  uses the field names implied by spec §5 with sensible fallbacks; revisit once
  a sample payload is available.
"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.freshsales.client import FreshsalesClient
from app.freshsales.parsing import parse_iso_date, parse_iso_timestamp, split_custom_fields
from app.repositories import deals_repo, reference_repo

logger = get_logger(__name__)


def _extract_custom_fields(deal: dict[str, Any]) -> dict[str, Any]:
    return deal.get("custom_field") or deal.get("custom_fields") or {}


def _extract_sales_account(deal: dict[str, Any]) -> tuple[int | None, str | None]:
    sales_account = deal.get("sales_account")
    if isinstance(sales_account, dict):
        return sales_account.get("id"), sales_account.get("name")
    return deal.get("sales_account_id"), deal.get("sales_account_name")


def _parse_view_deal(pipeline_id: int, deal: dict[str, Any]) -> dict[str, Any]:
    """Map a `/api/deals/view/{view_id}` record onto `deals_snapshot` columns."""
    curated_cf, remaining_cf = split_custom_fields(_extract_custom_fields(deal))
    sales_account_id, sales_account_name = _extract_sales_account(deal)

    data: dict[str, Any] = {
        "pipeline_id": pipeline_id,
        "stage_id": deal.get("stage_id") or deal.get("deal_stage_id"),
        "owner_id": deal.get("owner_id"),
        "name": deal.get("name"),
        "amount": deal.get("amount"),
        "base_currency_amount": deal.get("base_currency_amount"),
        "expected_close_date": parse_iso_date(deal.get("expected_close")),
        "stage_updated_at": parse_iso_timestamp(deal.get("stage_updated_time")),
        "age_days": deal.get("age"),
        "rotten_days": deal.get("rotten_days"),
        "sales_account_id": sales_account_id,
        "sales_account_name": sales_account_name,
        "custom_fields": remaining_cf,
        "raw_payload": deal,
        **curated_cf,
    }
    data = {k: v for k, v in data.items() if v is not None}
    data["deal_id"] = deal["id"]
    return data


async def run_deal_sync(session: AsyncSession, client: FreshsalesClient) -> None:
    """Paginate each active pipeline's view and upsert `deals_snapshot`."""
    pipelines = await reference_repo.list_pipelines(session, active_only=True)

    synced = 0
    for pipeline in pipelines:
        if pipeline.view_id is None:
            logger.warning("skipping deal sync, view_id not configured", pipeline_id=pipeline.id)
            continue

        async for deal in client.paginate_view(pipeline.view_id):
            await deals_repo.upsert_deal(session, _parse_view_deal(pipeline.id, deal))
            synced += 1

        await session.commit()

    logger.info("deal sync complete", deal_count=synced, pipeline_count=len(pipelines))
