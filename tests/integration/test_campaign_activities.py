"""GET /campaigns/{id}/activities — the NOG Activities page data.

Seeds `contact_activity` rows directly (the Freshsales sync is exercised elsewhere)
and drives the endpoint over HTTP so role-gating, the query params, and the
date/owner/tier filtering all run exactly as a client hits them.
"""

from datetime import UTC, datetime

import httpx
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.core.database import get_session
from app.main import create_app
from app.models.campaign import STATUS_ACTIVE, Campaign
from app.models.contact_activity import ContactActivity
from app.models.dashboard_user import DashboardUser
from app.repositories import campaigns_repo


async def _seed(session: AsyncSession) -> Campaign:
    campaign = await campaigns_repo.create(
        session, Campaign(slug="nog-2026", name="NOG", status=STATUS_ACTIVE, config={})
    )

    def _row(sk, owner, tier, kind, day, direction=None):
        return ContactActivity(
            campaign_id=campaign.id,
            contact_id=1000 + int(sk.split(":")[1]),
            contact_name="A Contact",
            owner_name=owner,
            prospect_tier=tier,
            activity_type=kind,
            direction=direction,
            occurred_at=datetime(2026, 7, day, 12, 0, tzinfo=UTC),
            subject="s",
            source_key=sk,
        )

    session.add_all(
        [
            _row("email:1", "Jennifer Obute", "Strategic", "email", 5, "outgoing"),
            _row("email:2", "Jennifer Obute", "Strategic", "email", 8, "incoming"),
            _row("call:3", "Jennifer Obute", "Strategic", "call", 8),
            _row("note:4", "Clinton Osuji", "Strategic", "note", 9),
            _row("email:5", None, "Standard", "email", 8),  # Unassigned / Standard
            _row("email:6", "Jennifer Obute", "Strategic", "email", 20),  # out of range
        ]
    )
    await session.commit()
    return campaign


@pytest_asyncio.fixture
async def client_as(db_session):
    app = create_app()

    async def _get_session():
        yield db_session

    app.dependency_overrides[get_session] = _get_session

    def _make(role: str = "admin"):
        user = DashboardUser(email="staff@churchgate.com", role=role, hashed_password="x")
        app.dependency_overrides[get_current_user] = lambda: user
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")

    return _make


async def test_activities_summary_in_range(client_as, db_session):
    campaign = await _seed(db_session)
    async with client_as("admin") as c:
        res = await c.get(
            f"/api/v1/campaigns/{campaign.id}/activities",
            params={"from": "2026-07-01", "to": "2026-07-10"},
        )
    assert res.status_code == 200, res.text
    body = res.json()["activities"]
    # 5 rows in range (the day-20 email is excluded)
    assert body["total"] == 5
    summ = {r["owner_name"]: r for r in body["summary"]}
    assert summ["Jennifer Obute"]["email"] == 2
    assert summ["Jennifer Obute"]["call"] == 1
    assert summ["Jennifer Obute"]["total"] == 3
    assert summ["Clinton Osuji"]["note"] == 1
    assert summ["Unassigned"]["email"] == 1
    assert set(body["owners"]) >= {"Jennifer Obute", "Clinton Osuji", "Unassigned"}
    assert body["tiers"] == ["Strategic", "Standard"]


async def test_activities_owner_filter(client_as, db_session):
    campaign = await _seed(db_session)
    async with client_as("admin") as c:
        res = await c.get(
            f"/api/v1/campaigns/{campaign.id}/activities",
            params={"from": "2026-07-01", "to": "2026-07-10", "owner": "Jennifer Obute"},
        )
    body = res.json()["activities"]
    assert body["total"] == 3
    assert [r["owner_name"] for r in body["summary"]] == ["Jennifer Obute"]


async def test_activities_tier_filter_excludes_standard(client_as, db_session):
    campaign = await _seed(db_session)
    async with client_as("admin") as c:
        res = await c.get(
            f"/api/v1/campaigns/{campaign.id}/activities",
            params={"from": "2026-07-01", "to": "2026-07-10", "tier": "Strategic"},
        )
    body = res.json()["activities"]
    # the lone Standard/Unassigned email drops out
    assert body["total"] == 4
    assert all(r["owner_name"] != "Unassigned" for r in body["summary"])


async def test_activities_narrow_date_range(client_as, db_session):
    campaign = await _seed(db_session)
    async with client_as("admin") as c:
        res = await c.get(
            f"/api/v1/campaigns/{campaign.id}/activities",
            params={"from": "2026-07-05", "to": "2026-07-05"},
        )
    body = res.json()["activities"]
    assert body["total"] == 1  # only the July-5 email
