import httpx
import respx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.freshsales.client import FreshsalesClient
from app.models.deal_reason import DealReason
from app.repositories.analytics_repo import categorize_loss_reason
from app.services.reference_sync import sync_deal_reasons

BASE_URL = "https://rbpropertieslimited.myfreshworks.com"


def test_categorize_loss_reason_exact_map() -> None:
    assert categorize_loss_reason("Price is too high") == "pricing"
    assert categorize_loss_reason("Opted our rival") == "competitor"
    assert categorize_loss_reason("No proper follow-up") == "poor follow-up"
    assert categorize_loss_reason("  need only in future ") == "timing"  # trimmed + case
    assert categorize_loss_reason("Some brand new reason") == "uncategorised"
    assert categorize_loss_reason(None) == "no reason recorded"
    assert categorize_loss_reason("   ") == "no reason recorded"


async def test_sync_deal_reasons_upserts_lookup(db_session: AsyncSession) -> None:
    payload = {
        "deal_reasons": [
            {"id": 500, "name": "Price is too high", "position": 1},
            {"id": 501, "name": "Opted our rival", "position": 2},
        ]
    }
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        router.get(url__regex=r".*/selector/deal_reasons").mock(
            return_value=httpx.Response(200, json=payload)
        )
        async with FreshsalesClient() as client:
            await sync_deal_reasons(db_session, client)

    rows = {r.id: r.name for r in (await db_session.execute(select(DealReason))).scalars().all()}
    assert rows == {500: "Price is too high", 501: "Opted our rival"}
