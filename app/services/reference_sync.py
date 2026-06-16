from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.freshsales.client import FreshsalesClient
from app.freshsales.parsing import PipelineStageResolver
from app.repositories import reference_repo

logger = get_logger(__name__)

# Business-line grouping (spec §1) — not returned by the Freshsales API, so it's
# maintained here as a static lookup.
PIPELINE_BUSINESS_LINES: dict[int, str] = {
    17000029646: "WTC Abuja - Leasing",
    17000074543: "WTC Abuja - Leasing (Renewals)",
    17000075032: "WTC Abuja - Sales",
    17000074560: "Churchgate - Trade & Investment Mission",
    17000074819: "Churchgate - Membership Club",
    17000074820: "Churchgate - Membership Club",
}

# "Test Pipeline - Do Not Use (Stan)" — excluded from v1 (spec §1).
EXCLUDED_PIPELINE_IDS: set[int] = {17000075034}

DEFAULT_BUSINESS_LINE = "Unclassified"


async def sync_pipelines_and_stages(session: AsyncSession, client: FreshsalesClient) -> None:
    """Sync `pipelines` + `stages` reference tables (spec §6B / daily refresh).

    The Test pipeline is marked `is_active=false` and its stages are skipped.
    """
    pipelines_data = await client.get_pipelines()
    pipelines = pipelines_data.get("deal_pipelines", [])

    for pipeline in pipelines:
        pipeline_id = pipeline["id"]
        is_active = pipeline_id not in EXCLUDED_PIPELINE_IDS
        await reference_repo.upsert_pipeline(
            session,
            {
                "id": pipeline_id,
                "name": pipeline["name"],
                "business_line": PIPELINE_BUSINESS_LINES.get(pipeline_id, DEFAULT_BUSINESS_LINE),
                "is_default": bool(pipeline.get("default", False)),
                "is_active": is_active,
            },
        )

        if not is_active:
            logger.info("skipping stages for inactive pipeline", pipeline_id=pipeline_id)
            continue

        # Classic API embeds stages inside each pipeline object
        for stage in pipeline.get("deal_stages", []):
            await reference_repo.upsert_stage(
                session,
                {
                    "id": stage["id"],
                    "pipeline_id": stage.get("deal_pipeline_id", pipeline_id),
                    "name": stage["name"],
                    "position": stage["position"],
                    "forecast_type": stage["forecast_type"],
                    "probability": stage.get("probability"),
                },
            )

    await session.commit()
    logger.info("pipeline/stage reference sync complete", pipeline_count=len(pipelines))


async def sync_owners(session: AsyncSession, client: FreshsalesClient) -> None:
    """Sync `owners` reference table (spec §6B / daily refresh)."""
    owners_data = await client.get_owners()
    owners = owners_data.get("owners") or owners_data.get("users", [])

    for owner in owners:
        await reference_repo.upsert_owner(
            session,
            {
                "id": owner["id"],
                "display_name": owner.get("name") or owner.get("display_name", ""),
                "email": owner.get("email"),
                "is_active": owner.get("is_active", True),
            },
        )

    await session.commit()
    logger.info("owner reference sync complete", owner_count=len(owners))


async def build_stage_resolver(session: AsyncSession) -> PipelineStageResolver:
    """Build the `(pipeline_name, stage_name) -> ids` resolver from current reference data."""
    pipelines = await reference_repo.list_pipelines(session)
    stages = await reference_repo.list_stages(session)
    return PipelineStageResolver(pipelines, stages)


async def run_reference_sync(
    session: AsyncSession, client: FreshsalesClient
) -> PipelineStageResolver:
    """Full reference sync: pipelines, stages, owners. Returns a fresh resolver."""
    await sync_pipelines_and_stages(session, client)
    await sync_owners(session, client)
    return await build_stage_resolver(session)
