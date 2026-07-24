"""POST /trade/programs/{slug}/register — the public wtcabuja.com capture
endpoint. Driven over HTTP (unauthenticated) so validation, dedup-merge, and
the primary/2nd-participant split all run exactly as the live form hits it.
"""

from unittest.mock import AsyncMock

import httpx
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.main import create_app
from app.models.trade_program import STATUS_ACTIVE, TradeProgram
from app.repositories import trade_repo


@pytest_asyncio.fixture
async def client(db_session):
    app = create_app()

    async def _get_session():
        yield db_session

    app.dependency_overrides[get_session] = _get_session
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _seed_program(session: AsyncSession) -> TradeProgram:
    return await trade_repo.create_program(
        session,
        TradeProgram(
            slug="export-launchpad-2026", name="Export Launchpad", status=STATUS_ACTIVE, config={}
        ),
    )


def _payload(**overrides):
    data = {
        "first_name": "Amaka",
        "last_name": "Eze",
        "email": "amaka@example.com",
        "phone": "+2348012345678",
        "company": "Depafek Foods",
        "responses": {"city": "Abuja"},
    }
    data.update(overrides)
    return data


async def test_register_creates_primary_only(client, db_session):
    await _seed_program(db_session)
    async with client as c:
        res = await c.post(
            "/api/v1/trade/programs/export-launchpad-2026/register", json=_payload()
        )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["created"] is True
    assert len(body["registration"]["participants"]) == 1
    assert body["registration"]["participants"][0]["is_primary"] is True


async def test_register_creates_second_participant_with_shared_company(client, db_session):
    await _seed_program(db_session)
    payload = _payload(
        responses={
            "city": "Abuja",
            "second_participant": {
                "first_name": "Goodness",
                "last_name": "Alabi",
                "email": "goodness@example.com",
            },
        }
    )
    async with client as c:
        res = await c.post(
            "/api/v1/trade/programs/export-launchpad-2026/register", json=payload
        )
    assert res.status_code == 201, res.text
    participants = res.json()["registration"]["participants"]
    assert len(participants) == 2
    second = next(p for p in participants if not p["is_primary"])
    # Regression: the 2nd participant must inherit the shared `company` field
    # from the primary/top-level payload, not land null.
    assert second["company"] == "Depafek Foods"
    assert second["city"] == "Abuja"
    assert second["crm_sync_status"] == "pending"


async def test_register_resubmit_merges_instead_of_duplicating(client, db_session):
    await _seed_program(db_session)
    async with client as c:
        first = await c.post(
            "/api/v1/trade/programs/export-launchpad-2026/register", json=_payload()
        )
        second = await c.post(
            "/api/v1/trade/programs/export-launchpad-2026/register",
            json=_payload(job_title="Founder"),
        )
    assert first.json()["created"] is True
    assert second.json()["created"] is False
    assert first.json()["registration"]["participants"][0]["id"] == (
        second.json()["registration"]["participants"][0]["id"]
    )
    assert second.json()["registration"]["participants"][0]["job_title"] == "Founder"


async def test_register_sends_confirmation_only_on_new_registration(
    client, db_session, monkeypatch
):
    """Gated the same way campaigns.py gates the campaign-era version of this
    email: fires once on a genuinely new registration, never on an idempotent
    resubmit — otherwise a flaky-retry would spam the applicant."""
    mock_send = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "app.api.v1.endpoints.trade.trade_mailer.send_application_confirmation", mock_send
    )
    await trade_repo.create_program(
        db_session,
        TradeProgram(
            slug="export-launchpad-2026",
            name="Export Launchpad",
            status=STATUS_ACTIVE,
            config={"application_confirmation": {"subject": "Received"}},
        ),
    )
    async with client as c:
        first = await c.post(
            "/api/v1/trade/programs/export-launchpad-2026/register", json=_payload()
        )
        second = await c.post(
            "/api/v1/trade/programs/export-launchpad-2026/register", json=_payload()
        )
    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    mock_send.assert_awaited_once()


async def test_register_applies_program_base_tags(client, db_session):
    await trade_repo.create_program(
        db_session,
        TradeProgram(
            slug="export-launchpad-2026",
            name="Export Launchpad",
            status=STATUS_ACTIVE,
            config={"base_tags": ["Export Launchpad", "2026 First Cohort"]},
        ),
    )
    payload = _payload(
        responses={
            "second_participant": {
                "first_name": "Goodness",
                "last_name": "Alabi",
                "email": "goodness@example.com",
            },
        }
    )
    async with client as c:
        res = await c.post(
            "/api/v1/trade/programs/export-launchpad-2026/register", json=payload
        )
    assert res.status_code == 201, res.text
    participants = res.json()["registration"]["participants"]
    primary = next(p for p in participants if p["is_primary"])
    second = next(p for p in participants if not p["is_primary"])
    assert primary["tags"] == ["Export Launchpad", "2026 First Cohort"]
    assert second["tags"] == ["Export Launchpad", "2026 First Cohort", "Second Participant"]


async def test_register_404_for_unknown_program(client, db_session):
    async with client as c:
        res = await c.post(
            "/api/v1/trade/programs/does-not-exist/register", json=_payload()
        )
    assert res.status_code == 404


async def test_register_409_for_inactive_program(client, db_session):
    program = await _seed_program(db_session)
    program.status = "draft"
    db_session.add(program)
    await db_session.commit()
    async with client as c:
        res = await c.post(
            "/api/v1/trade/programs/export-launchpad-2026/register", json=_payload()
        )
    assert res.status_code == 409
