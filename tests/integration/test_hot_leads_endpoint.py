"""GET /campaigns/leads/hot and PATCH /campaigns/leads/{id}/triage — the
cross-campaign Hot Leads queue. Driven over HTTP so role-gating, query
params, and the triage write all run exactly as a client hits them.
"""

import httpx
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.core.database import get_session
from app.main import create_app
from app.models.campaign import STATUS_ACTIVE, Campaign
from app.models.dashboard_user import DashboardUser
from app.models.lead import TRIAGE_CONTACTED
from app.repositories import campaigns_repo, leads_repo
from app.schemas.campaigns import LeadCreateRequest
from app.services import lead_service


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


def _lead_payload(email: str, **overrides) -> LeadCreateRequest:
    data = {
        "first_name": "Ada", "last_name": "Lovelace", "email": email,
        "phone": "+2348012345678", "company": "Energy Co",
    }
    data.update(overrides)
    return LeadCreateRequest(**data)


async def _seed_two_campaigns(session: AsyncSession):
    nog = await campaigns_repo.create(
        session, Campaign(slug="nog-2026", name="NOG", status=STATUS_ACTIVE, config={})
    )
    web = await campaigns_repo.create(
        session, Campaign(slug="wtcabuja-website", name="Website", status=STATUS_ACTIVE, config={})
    )
    nog_lead = await lead_service.capture_lead(
        session, "nog-2026", _lead_payload("nog@example.com", inspection_requested=True)
    )
    web_lead = await lead_service.capture_lead(
        session, "wtcabuja-website", _lead_payload("web@example.com", inspection_requested=True)
    )
    return nog, web, nog_lead, web_lead


async def test_list_hot_leads_requires_admin_role(client_as, db_session):
    await _seed_two_campaigns(db_session)
    async with client_as("rep") as c:
        res = await c.get("/api/v1/campaigns/leads/hot")
    assert res.status_code == 403


async def test_list_hot_leads_spans_campaigns_over_http(client_as, db_session):
    _, _, nog_lead, web_lead = await _seed_two_campaigns(db_session)
    async with client_as("admin") as c:
        res = await c.get("/api/v1/campaigns/leads/hot")
    assert res.status_code == 200, res.text
    body = res.json()
    ids = {row["id"] for row in body["leads"]}
    assert nog_lead.id in ids
    assert web_lead.id in ids
    assert body["total"] >= 2


async def test_list_hot_leads_filters_by_query_params(client_as, db_session):
    await _seed_two_campaigns(db_session)
    async with client_as("admin") as c:
        res = await c.get("/api/v1/campaigns/leads/hot", params={"opened": "true"})
    assert res.status_code == 200, res.text
    # Neither seeded lead has been opened yet.
    assert res.json()["total"] == 0


async def test_triage_update_requires_admin_role(client_as, db_session):
    _, _, nog_lead, _ = await _seed_two_campaigns(db_session)
    async with client_as("rep") as c:
        res = await c.patch(
            f"/api/v1/campaigns/leads/{nog_lead.id}/triage", json={"status": "contacted"}
        )
    assert res.status_code == 403


async def test_triage_update_records_status_and_actor_over_http(client_as, db_session):
    _, _, nog_lead, _ = await _seed_two_campaigns(db_session)
    async with client_as("admin") as c:
        res = await c.patch(
            f"/api/v1/campaigns/leads/{nog_lead.id}/triage", json={"status": "contacted"}
        )
    assert res.status_code == 200, res.text
    body = res.json()["lead"]
    assert body["triage_status"] == TRIAGE_CONTACTED
    assert body["triage_by"] == "staff@churchgate.com"
    assert body["triage_at"] is not None

    await db_session.refresh(nog_lead)
    assert nog_lead.triage_status == TRIAGE_CONTACTED


async def test_triage_update_rejects_invalid_status(client_as, db_session):
    _, _, nog_lead, _ = await _seed_two_campaigns(db_session)
    async with client_as("admin") as c:
        res = await c.patch(
            f"/api/v1/campaigns/leads/{nog_lead.id}/triage", json={"status": "definitely-maybe"}
        )
    assert res.status_code == 422


async def test_triage_update_404_for_unknown_lead(client_as, db_session):
    await _seed_two_campaigns(db_session)
    async with client_as("admin") as c:
        res = await c.patch(
            "/api/v1/campaigns/leads/999999/triage", json={"status": "contacted"}
        )
    assert res.status_code == 404


async def test_hot_leads_response_excludes_dismissed_when_uncontacted_filter_set(
    client_as, db_session
):
    _, _, nog_lead, web_lead = await _seed_two_campaigns(db_session)
    await leads_repo.set_triage(
        db_session, nog_lead, status=TRIAGE_CONTACTED, by="rep@example.com"
    )
    async with client_as("admin") as c:
        res = await c.get("/api/v1/campaigns/leads/hot", params={"uncontacted": "true"})
    ids = {row["id"] for row in res.json()["leads"]}
    assert web_lead.id in ids
    assert nog_lead.id not in ids
