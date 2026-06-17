from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.freshsales.parsing import (
    PipelineStageResolver,
    parse_webhook_date,
    parse_webhook_timestamp,
    split_custom_fields,
)
from app.repositories import deals_repo, events_repo, reference_repo
from app.schemas.webhook import FreshsalesDealWebhook
from app.services.change_detection import detect_changes

logger = get_logger(__name__)

# Recommended webhook field selection (spec §6A): trim most deal_sales_account_*
# fields, keep only id/name.
_SALES_ACCOUNT_KEEP_KEYS = {"deal_sales_account_id", "deal_sales_account_name"}


def _trim_raw_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in payload.items()
        if not key.startswith("deal_sales_account_") or key in _SALES_ACCOUNT_KEEP_KEYS
    }


def _extract_custom_fields(payload: dict[str, Any]) -> dict[str, Any]:
    """Extract `deal_cf_*` keys, stripping the `deal_` prefix to get `cf_*`."""
    return {
        key.removeprefix("deal_"): value
        for key, value in payload.items()
        if key.startswith("deal_cf_")
    }


async def ingest_webhook(
    session: AsyncSession,
    payload: dict[str, Any],
    resolver: PipelineStageResolver,
) -> None:
    """Webhook ingest (spec §6A).

    1. Resolve pipeline/stage names -> ids via the reference-table resolver.
    2. Load the existing snapshot for `deal_id` (if any).
    3. Diff old vs new (pipeline_id, stage_id) and owner_id -> insert `deal_events`.
    4. Upsert `deals_snapshot` with the new current state.
    """
    webhook = FreshsalesDealWebhook.model_validate(payload)
    existing = await deals_repo.get_deal(session, webhook.deal_id)

    resolution = None
    if webhook.deal_deal_pipeline_name and webhook.deal_deal_stage_name:
        resolution = resolver.resolve(
            webhook.deal_deal_pipeline_name, webhook.deal_deal_stage_name
        )

    if resolution is not None:
        new_pipeline_id: int | None = resolution.pipeline_id
        new_stage_id: int | None = resolution.stage_id
    elif existing is not None:
        # Unresolvable names on an update: keep the prior values rather than
        # recording a spurious stage_change to "unknown".
        new_pipeline_id = existing.pipeline_id
        new_stage_id = existing.stage_id
        logger.warning(
            "could not resolve pipeline/stage from webhook names, keeping existing",
            pipeline_name=webhook.deal_deal_pipeline_name,
            stage_name=webhook.deal_deal_stage_name,
            deal_id=webhook.deal_id,
        )
    else:
        new_pipeline_id = None
        new_stage_id = None
        logger.warning(
            "could not resolve pipeline/stage for new deal",
            pipeline_name=webhook.deal_deal_pipeline_name,
            stage_name=webhook.deal_deal_stage_name,
            deal_id=webhook.deal_id,
        )

    known_owner_ids = {o.id for o in await reference_repo.list_owners(session)}
    raw_owner_id = webhook.deal_owner_id if webhook.deal_owner_id is not None else (existing.owner_id if existing else None)
    new_owner_id: int | None = raw_owner_id if raw_owner_id in known_owner_ids else None

    occurred_at = parse_webhook_timestamp(webhook.deal_stage_updated_time) or datetime.now(UTC)

    events = detect_changes(
        deal_id=webhook.deal_id,
        existing=existing,
        new_pipeline_id=new_pipeline_id,
        new_stage_id=new_stage_id,
        new_owner_id=new_owner_id,
        occurred_at=occurred_at,
        source="webhook",
        raw_payload=payload,
    )
    for event in events:
        await events_repo.insert_event(session, event)

    curated_cf, remaining_cf = split_custom_fields(_extract_custom_fields(payload))

    deal_data: dict[str, Any] = {
        "deal_id": webhook.deal_id,
        "pipeline_id": new_pipeline_id,
        "stage_id": new_stage_id,
        "owner_id": new_owner_id,
        "name": webhook.deal_name,
        "amount": webhook.deal_amount,
        "base_currency_amount": webhook.deal_base_currency_amount,
        "expected_close_date": parse_webhook_date(webhook.deal_expected_close),
        "stage_updated_at": occurred_at,
        "deal_created_at": parse_webhook_timestamp(webhook.deal_created_at),
        "lost_reason": webhook.deal_lost_reason,
        "lost_reason_id": webhook.deal_lost_reason_id,
        "sales_account_id": webhook.deal_sales_account_id,
        "sales_account_name": webhook.deal_sales_account_name,
        "custom_fields": remaining_cf,
        "raw_payload": _trim_raw_payload(payload),
        **curated_cf,
    }
    deal_data = {k: v for k, v in deal_data.items() if v is not None}
    deal_data["deal_id"] = webhook.deal_id

    await deals_repo.upsert_deal(session, deal_data)
    await session.commit()

    logger.info(
        "webhook ingested",
        deal_id=webhook.deal_id,
        event_types=[e.event_type for e in events],
    )
