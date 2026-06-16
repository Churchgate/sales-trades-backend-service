import httpx
import respx
from sqlalchemy.ext.asyncio import AsyncSession

from app.freshsales.client import FreshsalesClient
from app.repositories import reference_repo
from app.services.reference_sync import EXCLUDED_PIPELINE_IDS, run_reference_sync

BASE_URL = "https://rbpropertieslimited.myfreshworks.com"


async def test_reference_sync_excludes_test_pipeline_and_builds_resolver(
    db_session: AsyncSession,
) -> None:
    test_pipeline_id = next(iter(EXCLUDED_PIPELINE_IDS))

    with respx.mock(base_url=BASE_URL, assert_all_called=True) as mock_router:
        # Suite /selector/deal_pipelines embeds deal_stages inside each pipeline object
        mock_router.get("/crm/sales/api/selector/deal_pipelines").mock(
            return_value=httpx.Response(
                200,
                json={
                    "deal_pipelines": [
                        {
                            "id": 17000029646,
                            "name": "New Rental Pipeline",
                            "default": True,
                            "deal_stages": [
                                {
                                    "id": 1,
                                    "name": "New",
                                    "position": 1,
                                    "forecast_type": "Open",
                                    "probability": 10,
                                    "deal_pipeline_id": 17000029646,
                                },
                                {
                                    "id": 2,
                                    "name": "Won",
                                    "position": 2,
                                    "forecast_type": "Closed Won",
                                    "probability": 100,
                                    "deal_pipeline_id": 17000029646,
                                },
                            ],
                        },
                        {
                            "id": test_pipeline_id,
                            "name": "Test Pipeline - Do Not Use (Stan)",
                            "default": False,
                            "deal_stages": [],
                        },
                    ]
                },
            )
        )
        mock_router.get("/crm/sales/api/selector/owners").mock(
            return_value=httpx.Response(
                200, json={"users": [{"id": 100, "name": "Vinay", "email": "vinay@example.com"}]}
            )
        )

        async with FreshsalesClient() as client:
            resolver = await run_reference_sync(db_session, client)

    pipelines = {p.id: p for p in await reference_repo.list_pipelines(db_session)}
    assert pipelines[17000029646].is_active is True
    assert pipelines[17000029646].business_line == "WTC Abuja - Leasing"
    assert pipelines[test_pipeline_id].is_active is False

    stages = await reference_repo.list_stages(db_session, pipeline_id=17000029646)
    assert {s.name for s in stages} == {"New", "Won"}

    owners = await reference_repo.list_owners(db_session)
    assert owners[0].display_name == "Vinay"

    resolution = resolver.resolve("New Rental Pipeline", "Won")
    assert resolution is not None
    assert resolution.stage_id == 2
