"""GET /trade/programs, /trade/programs/{id}, /trade/programs/{id}/participants,
/trade/registrations/{id} — driven over HTTP so role-gating and the
participant-pairing logic run exactly as a client hits them.
"""

import httpx
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.core.database import get_session
from app.main import create_app
from app.models.dashboard_user import DashboardUser
from app.models.trade_lead import CRM_PENDING, CRM_SYNCED, TradeLead
from app.models.trade_program import STATUS_ACTIVE, TradeProgram
from app.repositories import trade_repo


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


async def _seed_registration(session: AsyncSession):
    program = await trade_repo.create_program(
        session,
        TradeProgram(
            slug="export-launchpad-2026", name="Export Launchpad", status=STATUS_ACTIVE, config={}
        ),
    )
    primary = await trade_repo.create_lead(
        session,
        TradeLead(
            trade_program_id=program.id,
            registration_id="reg-1",
            participant_index=1,
            is_primary=True,
            first_name="Amaka",
            last_name="Eze",
            email="amaka@example.com",
            crm_sync_status=CRM_SYNCED,
            crm_contact_id="12345",
        ),
    )
    second = await trade_repo.create_lead(
        session,
        TradeLead(
            trade_program_id=program.id,
            registration_id="reg-1",
            participant_index=2,
            is_primary=False,
            first_name="Goodness",
            last_name="Alabi",
            email="goodness@example.com",
            crm_sync_status=CRM_PENDING,
        ),
    )
    return program, primary, second


async def test_list_programs_requires_staff_role(client_as, db_session):
    await _seed_registration(db_session)
    async with client_as("rep") as c:
        res = await c.get("/api/v1/trade/programs")
    assert res.status_code == 200, res.text  # view-all: every staff role can read


async def test_list_programs_rejects_unauthenticated(client_as):
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        res = await c.get("/api/v1/trade/programs")
    assert res.status_code in (401, 403)


async def test_list_programs_returns_seeded_program(client_as, db_session):
    program, _, _ = await _seed_registration(db_session)
    async with client_as("admin") as c:
        res = await c.get("/api/v1/trade/programs")
    assert res.status_code == 200, res.text
    slugs = {p["slug"] for p in res.json()["programs"]}
    assert program.slug in slugs


async def test_get_program_stats_count_registrations_and_participants(client_as, db_session):
    program, _, _ = await _seed_registration(db_session)
    async with client_as("admin") as c:
        res = await c.get(f"/api/v1/trade/programs/{program.id}")
    assert res.status_code == 200, res.text
    stats = res.json()["stats"]
    assert stats["total_registrations"] == 1
    assert stats["total_participants"] == 2
    assert stats["crm_sync_breakdown"] == {"synced": 1, "pending": 1}


async def test_list_participants_includes_co_participant_reference(client_as, db_session):
    program, primary, second = await _seed_registration(db_session)
    async with client_as("admin") as c:
        res = await c.get(f"/api/v1/trade/programs/{program.id}/participants")
    assert res.status_code == 200, res.text
    by_id = {row["id"]: row for row in res.json()["leads"]}
    assert by_id[primary.id]["co_participant"]["id"] == second.id
    assert by_id[primary.id]["co_participant"]["is_primary"] is False
    assert by_id[second.id]["co_participant"]["id"] == primary.id


async def test_get_registration_returns_both_participants(client_as, db_session):
    program, primary, second = await _seed_registration(db_session)
    async with client_as("admin") as c:
        res = await c.get("/api/v1/trade/registrations/reg-1")
    assert res.status_code == 200, res.text
    body = res.json()["registration"]
    ids = {p["id"] for p in body["participants"]}
    assert ids == {primary.id, second.id}


async def test_get_registration_404_for_unknown_id(client_as, db_session):
    await _seed_registration(db_session)
    async with client_as("admin") as c:
        res = await c.get("/api/v1/trade/registrations/does-not-exist")
    assert res.status_code == 404


async def test_list_participants_filters_by_crm_sync_status(client_as, db_session):
    program, primary, second = await _seed_registration(db_session)
    async with client_as("admin") as c:
        res = await c.get(
            f"/api/v1/trade/programs/{program.id}/participants",
            params={"crm_sync_status": "pending"},
        )
    assert res.status_code == 200, res.text
    ids = {row["id"] for row in res.json()["leads"]}
    assert ids == {second.id}
