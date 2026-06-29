"""POST /webhooks/agent/log — the logging-agent write endpoint. Freshsales is mocked
with respx; the outer ASGI client drives the app. Covers the happy path, the
pipeline allow-list guard, and secret auth."""

import httpx
import respx

from app.core.config import get_settings
from app.main import create_app

BASE = "https://rbpropertieslimited.myfreshworks.com"
TEST_PIPELINE = 17000075034  # default allow-list (Settings.agent_allowed_pipeline_ids)
DEAL_ID = 17036546194


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()), base_url="http://test")


def _deal(pipeline_id: int) -> dict:
    return {"deal": {"id": DEAL_ID, "name": "Phantom AI deal",
                     "deal_pipeline_id": pipeline_id, "owner_id": 17000101729}}


async def test_logs_note_and_task_on_allowed_pipeline(monkeypatch):
    monkeypatch.setattr(get_settings(), "agent_webhook_secret", "s3cret")
    with respx.mock(base_url=BASE, assert_all_called=False) as r:
        r.get(url__regex=rf".*/deals/{DEAL_ID}.*").mock(
            return_value=httpx.Response(200, json=_deal(TEST_PIPELINE)))
        r.post(url__regex=r".*/crm/sales/api/notes").mock(
            return_value=httpx.Response(200, json={"note": {"id": 111}}))
        r.post(url__regex=r".*/crm/sales/api/tasks").mock(
            return_value=httpx.Response(201, json={"task": {"id": 222}}))
        async with _client() as c:
            res = await c.post(
                "/webhooks/agent/log",
                headers={"X-Agent-Secret": "s3cret"},
                json={"intent": "note+task", "deal_id": DEAL_ID,
                      "note_text": "Met MHC, want revised proposal",
                      "task_title": "Send revised proposal", "due_date": "2026-07-03"},
            )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["note_id"] == 111
    assert body["task_id"] == 222
    assert "Phantom AI deal" in body["confirmation"]


async def test_refuses_deal_outside_allowed_pipeline(monkeypatch):
    monkeypatch.setattr(get_settings(), "agent_webhook_secret", "s3cret")
    with respx.mock(base_url=BASE, assert_all_called=False) as r:
        # deal is in a real pipeline, not the allow-listed test one
        r.get(url__regex=rf".*/deals/{DEAL_ID}.*").mock(
            return_value=httpx.Response(200, json=_deal(17000029646)))
        notes = r.post(url__regex=r".*/crm/sales/api/notes").mock(
            return_value=httpx.Response(200, json={"note": {"id": 111}}))
        async with _client() as c:
            res = await c.post(
                "/webhooks/agent/log",
                headers={"X-Agent-Secret": "s3cret"},
                json={"intent": "note", "deal_id": DEAL_ID, "note_text": "should not write"},
            )
    assert res.status_code == 403, res.text
    assert not notes.called  # nothing written to Freshsales


async def test_bad_secret_is_401(monkeypatch):
    monkeypatch.setattr(get_settings(), "agent_webhook_secret", "s3cret")
    async with _client() as c:
        res = await c.post(
            "/webhooks/agent/log",
            headers={"X-Agent-Secret": "wrong"},
            json={"intent": "note", "deal_id": DEAL_ID, "note_text": "x"},
        )
    assert res.status_code == 401


async def test_unconfigured_is_503():
    # Default secret is empty → endpoint refuses (no unprotected writes).
    async with _client() as c:
        res = await c.post(
            "/webhooks/agent/log",
            headers={"X-Agent-Secret": "anything"},
            json={"intent": "note", "deal_id": DEAL_ID, "note_text": "x"},
        )
    assert res.status_code == 503


async def test_validation_rejects_missing_fields(monkeypatch):
    monkeypatch.setattr(get_settings(), "agent_webhook_secret", "s3cret")
    async with _client() as c:
        # task intent with no task_title
        res = await c.post(
            "/webhooks/agent/log",
            headers={"X-Agent-Secret": "s3cret"},
            json={"intent": "task", "deal_id": DEAL_ID},
        )
    assert res.status_code == 422
