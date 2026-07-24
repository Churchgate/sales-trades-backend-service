"""POST /trade/programs/{slug}/eligibility — public eligibility-document
upload, and the staff-side GET .../documents listing. trade_storage's actual
Supabase Storage calls are mocked throughout; this suite covers the
document/eligibility-rollup logic, not the storage integration itself.
"""

from unittest.mock import AsyncMock

import httpx
import pytest_asyncio

from app.api.dependencies import get_current_user
from app.core.database import get_session
from app.main import create_app
from app.models.dashboard_user import DashboardUser
from app.models.trade_lead import (
    ELIGIBILITY_NOT_REQUESTED,
    ELIGIBILITY_PENDING,
    ELIGIBILITY_SUBMITTED,
)
from app.models.trade_program import STATUS_ACTIVE, TradeProgram
from app.repositories import trade_repo

_REQUIRED_DOCUMENTS = [
    {"key": "cac_certificate", "label": "CAC Certificate", "required": True},
    {"key": "logo", "label": "Company Logo", "required": True},
    {"key": "company_profile", "label": "Company Profile", "required": False},
]


@pytest_asyncio.fixture
async def client(db_session):
    app = create_app()

    async def _get_session():
        yield db_session

    app.dependency_overrides[get_session] = _get_session
    user = DashboardUser(email="staff@churchgate.com", role="admin", hashed_password="x")
    app.dependency_overrides[get_current_user] = lambda: user
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest_asyncio.fixture(autouse=True)
def mock_storage(monkeypatch):
    monkeypatch.setattr(
        "app.services.trade_eligibility.trade_storage.upload_document", AsyncMock()
    )
    monkeypatch.setattr(
        "app.services.trade_eligibility.trade_storage.delete_document", AsyncMock()
    )
    monkeypatch.setattr(
        "app.services.trade_storage.get_download_url",
        AsyncMock(return_value="https://example.com/signed"),
    )
    monkeypatch.setattr(
        "app.api.v1.endpoints.trade.trade_storage.get_download_url",
        AsyncMock(return_value="https://example.com/signed"),
    )


async def _seed_registration(session, config=None):
    program = await trade_repo.create_program(
        session,
        TradeProgram(
            slug="export-launchpad-2026",
            name="Export Launchpad",
            status=STATUS_ACTIVE,
            config=config if config is not None else {"required_documents": _REQUIRED_DOCUMENTS},
        ),
    )
    from app.models.trade_lead import TradeLead

    lead = await trade_repo.create_lead(
        session,
        TradeLead(
            trade_program_id=program.id,
            registration_id="reg-1",
            first_name="Amaka",
            last_name="Eze",
            email="amaka@example.com",
        ),
    )
    return program, lead


def _upload(**overrides):
    data = {"registration_id": "reg-1", "document_key": "cac_certificate"}
    data.update(overrides)
    files = {"file": ("cac.pdf", b"%PDF-1.4 fake", "application/pdf")}
    return data, files


async def test_upload_unknown_program_404(client, db_session):
    data, files = _upload()
    async with client as c:
        res = await c.post(
            "/api/v1/trade/programs/does-not-exist/eligibility", data=data, files=files
        )
    assert res.status_code == 404


async def test_upload_unknown_registration_404(client, db_session):
    await _seed_registration(db_session)
    data, files = _upload(registration_id="no-such-reg")
    async with client as c:
        res = await c.post(
            "/api/v1/trade/programs/export-launchpad-2026/eligibility", data=data, files=files
        )
    assert res.status_code == 404


async def test_upload_unknown_document_key_400(client, db_session):
    await _seed_registration(db_session)
    data, files = _upload(document_key="not_a_real_key")
    async with client as c:
        res = await c.post(
            "/api/v1/trade/programs/export-launchpad-2026/eligibility", data=data, files=files
        )
    assert res.status_code == 400


async def test_upload_partial_then_complete_rollup(client, db_session):
    _, lead = await _seed_registration(db_session)
    assert lead.eligibility_status == ELIGIBILITY_NOT_REQUESTED

    async with client as c:
        data, files = _upload(document_key="cac_certificate")
        first = await c.post(
            "/api/v1/trade/programs/export-launchpad-2026/eligibility", data=data, files=files
        )
        data, files = _upload(document_key="logo")
        second = await c.post(
            "/api/v1/trade/programs/export-launchpad-2026/eligibility", data=data, files=files
        )
    assert first.status_code == 201, first.text
    assert first.json()["eligibility_status"] == ELIGIBILITY_PENDING
    assert second.status_code == 201, second.text
    assert second.json()["eligibility_status"] == ELIGIBILITY_SUBMITTED

    await db_session.refresh(lead)
    assert lead.eligibility_status == ELIGIBILITY_SUBMITTED
    assert lead.eligibility_submitted_at is not None


async def test_reupload_same_key_replaces_and_deletes_old_object(client, db_session, monkeypatch):
    await _seed_registration(db_session)
    delete_mock = AsyncMock()
    monkeypatch.setattr("app.services.trade_eligibility.trade_storage.delete_document", delete_mock)

    data, files = _upload()
    async with client as c:
        first = await c.post(
            "/api/v1/trade/programs/export-launchpad-2026/eligibility", data=data, files=files
        )
        second = await c.post(
            "/api/v1/trade/programs/export-launchpad-2026/eligibility", data=data, files=files
        )
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["document"]["id"] == second.json()["document"]["id"]
    delete_mock.assert_awaited_once()


async def test_list_registration_documents_includes_signed_url(client, db_session):
    await _seed_registration(db_session)
    data, files = _upload()
    async with client as c:
        await c.post(
            "/api/v1/trade/programs/export-launchpad-2026/eligibility", data=data, files=files
        )
        res = await c.get("/api/v1/trade/registrations/reg-1/documents")
    assert res.status_code == 200, res.text
    documents = res.json()["documents"]
    assert len(documents) == 1
    assert documents[0]["download_url"] == "https://example.com/signed"
