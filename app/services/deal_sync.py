"""Scheduled deal-view sync (spec §6B): reconciliation + `rotten_days` refresh.

Paginates the configured global deal smart-views (`Settings.deal_view_ids` —
Open/Won/Lost by default) and upserts `deals_snapshot`. This catches anything
webhooks missed and refreshes fields the webhook payload doesn't carry
(`rotten_days`, `age`).

Each deal record self-identifies its pipeline and stage (`deal_pipeline_id` /
`deal_stage_id`), so there is no per-pipeline view to configure. The views are
fetched with `include=owner` so `owner_id` is present. Deals whose pipeline is
not active (e.g. the Test pipeline) are skipped; stage/owner ids not present in
the synced reference tables are dropped to null to avoid FK violations.
"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
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


def _parse_view_deal(deal: dict[str, Any]) -> dict[str, Any]:
    """Map a deals/view record onto `deals_snapshot` columns.

    Pipeline and stage come from the record itself; `owner_id` is present only
    when the view is fetched with `include=owner`.
    """
    curated_cf, remaining_cf = split_custom_fields(_extract_custom_fields(deal))
    sales_account_id, sales_account_name = _extract_sales_account(deal)

    data: dict[str, Any] = {
        "pipeline_id": deal.get("deal_pipeline_id"),
        "stage_id": deal.get("deal_stage_id") or deal.get("stage_id"),
        "owner_id": deal.get("owner_id"),
        "name": deal.get("name"),
        "amount": deal.get("amount"),
        "base_currency_amount": deal.get("base_currency_amount"),
        "expected_close_date": parse_iso_date(deal.get("expected_close")),
        "stage_updated_at": parse_iso_timestamp(deal.get("stage_updated_time")),
        "deal_created_at": parse_iso_timestamp(deal.get("created_at")),
        "lost_reason": deal.get("lost_reason"),
        "lost_reason_id": deal.get("lost_reason_id"),
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
    """Upsert `deals_snapshot` for every active pipeline.

    Freshsales' system deal views are hard-scoped to the default pipeline, so:
    - default pipeline → paginate the configured views (efficient bulk);
    - other active pipelines → enumerate ids via filtered_search, then fetch each
      deal's full record (the only way to get rich fields for non-default deals).
    """
    settings = get_settings()
    pipelines = await reference_repo.list_pipelines(session, active_only=True)
    active_pipeline_ids = {p.id for p in pipelines}
    known_stage_ids = {s.id for s in await reference_repo.list_stages(session)}
    known_owner_ids = {o.id for o in await reference_repo.list_owners(session)}

    counters = {"synced": 0, "skipped": 0}

    async def _upsert(deal: dict[str, Any]) -> None:
        if deal.get("deal_pipeline_id") not in active_pipeline_ids:
            counters["skipped"] += 1
            return
        data = _parse_view_deal(deal)
        # Drop FK values we have no reference row for (deleted/legacy stages,
        # inactive owners) so the upsert can't violate a foreign key.
        if data.get("stage_id") not in known_stage_ids:
            data.pop("stage_id", None)
        if data.get("owner_id") not in known_owner_ids:
            data.pop("owner_id", None)
        await deals_repo.upsert_deal(session, data)
        counters["synced"] += 1

    # 1. Default pipeline — system views (Open/Won/Lost) cover it efficiently.
    for view_id in settings.deal_view_ids:
        async for deal in client.paginate_view(view_id):
            await _upsert(deal)
        await session.commit()

    # 2. Non-default active pipelines — filtered_search ids + full deal records.
    for pipeline in pipelines:
        if pipeline.is_default:
            continue
        async for deal_id in client.iter_pipeline_deal_ids(pipeline.id):
            await _upsert(await client.get_deal(deal_id))
        await session.commit()

    logger.info("deal sync complete", upserts=counters["synced"], skipped=counters["skipped"])
