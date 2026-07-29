"""PATCH /trade/participants/{lead_id}/review — the new admin approval gate.
Scoped tighter than the rest of the view-all trade admin surface (rep/hod/
team_lead can read, but only admin/superadmin can approve/reject), since this
directly controls what reaches Freshsales — see trade_crm_sync.sync_trade_lead.
"""

import httpx
import pytest_asyncio

from app.api.dependencies import get_current_user
from app.core.database import get_session
from app.main import create_app
from app.models.dashboard_user import DashboardUser
from app.models.trade_lead import REVIEW_APPROVED, REVIEW_PENDING
from tests.integration.test_trade_endpoints import _seed_registration


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


async def test_review_requires_admin_role(client_as, db_session):
    _, primary, _ = await _seed_registration(db_session)
    async with client_as("rep") as c:
        res = await c.patch(
            f"/api/v1/trade/participants/{primary.id}/review", json={"status": "approved"}
        )
    assert res.status_code == 403, res.text


async def test_review_rejects_unauthenticated(client_as, db_session):
    _, primary, _ = await _seed_registration(db_session)
    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        res = await c.patch(
            f"/api/v1/trade/participants/{primary.id}/review", json={"status": "approved"}
        )
    assert res.status_code in (401, 403)


async def test_admin_can_approve_participant(client_as, db_session):
    _, primary, _ = await _seed_registration(db_session)
    assert primary.review_status == REVIEW_PENDING

    async with client_as("admin") as c:
        res = await c.patch(
            f"/api/v1/trade/participants/{primary.id}/review", json={"status": "approved"}
        )
    assert res.status_code == 200, res.text
    body = res.json()["lead"]
    assert body["review_status"] == REVIEW_APPROVED
    assert body["reviewed_at"] is not None
    assert body["reviewed_by"] == "staff@churchgate.com"


async def test_superadmin_can_reject_participant(client_as, db_session):
    _, primary, _ = await _seed_registration(db_session)
    async with client_as("superadmin") as c:
        res = await c.patch(
            f"/api/v1/trade/participants/{primary.id}/review", json={"status": "rejected"}
        )
    assert res.status_code == 200, res.text
    assert res.json()["lead"]["review_status"] == "rejected"


async def test_review_404_for_unknown_participant(client_as, db_session):
    await _seed_registration(db_session)
    async with client_as("admin") as c:
        res = await c.patch(
            "/api/v1/trade/participants/999999/review", json={"status": "approved"}
        )
    assert res.status_code == 404, res.text


async def test_review_rejects_invalid_status_value(client_as, db_session):
    _, primary, _ = await _seed_registration(db_session)
    async with client_as("admin") as c:
        res = await c.patch(
            f"/api/v1/trade/participants/{primary.id}/review", json={"status": "maybe"}
        )
    assert res.status_code == 422, res.text
