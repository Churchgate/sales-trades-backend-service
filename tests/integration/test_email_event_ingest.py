import base64
import json

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.email_event import EmailEvent
from app.services import email_event_ingest, lead_service

from .test_campaigns import _lead, _make_campaign

_TS = 1751932800  # 2026-07-08T00:00:00Z


def _open_event(lead_id: int, sg_event_id: str = "evt-open-1") -> dict:
    return {
        "event": "open",
        "email": "ada@example.com",
        "timestamp": _TS,
        "sg_event_id": sg_event_id,
        "sg_message_id": "msg-1",
        "lead_id": str(lead_id),
        "email_kind": "pack",
    }


def _click_event(lead_id: int, url: str, sg_event_id: str = "evt-click-1") -> dict:
    return {
        "event": "click",
        "email": "ada@example.com",
        "timestamp": _TS,
        "sg_event_id": sg_event_id,
        "sg_message_id": "msg-1",
        "lead_id": str(lead_id),
        "email_kind": "pack",
        "url": url,
    }


async def test_ingest_open_event_sets_rollup_and_records_event(db_session: AsyncSession) -> None:
    await _make_campaign(db_session)
    lead = await lead_service.capture_lead(db_session, "nog-2026", _lead())
    assert lead.pack_opened_at is None

    processed = await email_event_ingest.ingest_events(db_session, [_open_event(lead.id)])
    assert processed == 1

    await db_session.refresh(lead)
    assert lead.pack_opened_at is not None
    assert lead.pack_opened_count == 1

    rows = (
        await db_session.execute(select(EmailEvent).where(EmailEvent.lead_id == lead.id))
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].event_type == "open"
    assert rows[0].email_kind == "pack"


async def test_ingest_click_event_resolves_material(db_session: AsyncSession) -> None:
    await _make_campaign(db_session)
    lead = await lead_service.capture_lead(db_session, "nog-2026", _lead())

    processed = await email_event_ingest.ingest_events(
        db_session, [_click_event(lead.id, "https://assets.example.com/prospectus.pdf")]
    )
    assert processed == 1

    await db_session.refresh(lead)
    assert lead.pack_clicked_materials == ["Corporate Prospectus"]

    row = (
        await db_session.execute(select(EmailEvent).where(EmailEvent.lead_id == lead.id))
    ).scalars().first()
    assert row.material == "Corporate Prospectus"
    assert row.url == "https://assets.example.com/prospectus.pdf"


async def test_ingest_click_on_unknown_url_records_event_without_material(
    db_session: AsyncSession,
) -> None:
    """A click on a link that isn't a known campaign asset is still recorded (full
    history), it just can't be attributed to a specific document."""
    await _make_campaign(db_session)
    lead = await lead_service.capture_lead(db_session, "nog-2026", _lead())

    processed = await email_event_ingest.ingest_events(
        db_session, [_click_event(lead.id, "https://wtcabuja.com/some-other-page")]
    )
    assert processed == 1

    await db_session.refresh(lead)
    assert lead.pack_clicked_materials is None

    row = (
        await db_session.execute(select(EmailEvent).where(EmailEvent.lead_id == lead.id))
    ).scalars().first()
    assert row.material is None


async def test_ingest_is_idempotent_on_duplicate_sg_event_id(db_session: AsyncSession) -> None:
    """SendGrid's webhook is at-least-once delivery — a re-POST of the same event
    must not double-count opens."""
    await _make_campaign(db_session)
    lead = await lead_service.capture_lead(db_session, "nog-2026", _lead())

    event = _open_event(lead.id, sg_event_id="evt-dup")
    first = await email_event_ingest.ingest_events(db_session, [event])
    second = await email_event_ingest.ingest_events(db_session, [event])
    assert first == 1
    assert second == 0

    await db_session.refresh(lead)
    assert lead.pack_opened_count == 1

    rows = (
        await db_session.execute(select(EmailEvent).where(EmailEvent.lead_id == lead.id))
    ).scalars().all()
    assert len(rows) == 1


async def test_ingest_skips_events_without_lead_id(db_session: AsyncSession) -> None:
    """Emails sent before custom_args existed have no lead_id — silently dropped,
    not attributed to the wrong lead."""
    event = {
        "event": "open",
        "email": "unknown@example.com",
        "timestamp": _TS,
        "sg_event_id": "evt-no-lead",
    }
    processed = await email_event_ingest.ingest_events(db_session, [event])
    assert processed == 0

    rows = (await db_session.execute(select(EmailEvent))).scalars().all()
    assert rows == []


async def test_ingest_skips_untracked_event_types(db_session: AsyncSession) -> None:
    await _make_campaign(db_session)
    lead = await lead_service.capture_lead(db_session, "nog-2026", _lead())
    event = {
        "event": "processed",  # not in _TRACKED_EVENTS
        "timestamp": _TS,
        "sg_event_id": "evt-processed",
        "lead_id": str(lead.id),
    }
    processed = await email_event_ingest.ingest_events(db_session, [event])
    assert processed == 0


async def test_ingest_one_bad_event_does_not_sink_the_batch(db_session: AsyncSession) -> None:
    await _make_campaign(db_session)
    lead = await lead_service.capture_lead(db_session, "nog-2026", _lead())
    malformed = {
        "event": "open",
        "timestamp": _TS,
        "sg_event_id": "evt-malformed",
        "lead_id": "not-a-number",
    }
    good = _open_event(lead.id, sg_event_id="evt-good")

    processed = await email_event_ingest.ingest_events(db_session, [malformed, good])
    assert processed == 1  # malformed skipped (non-numeric lead_id), good one still lands

    await db_session.refresh(lead)
    assert lead.pack_opened_count == 1


def test_verify_signature_accepts_valid_and_rejects_tampered() -> None:
    private_key = ec.generate_private_key(ec.SECP256R1())
    public_key_b64 = base64.b64encode(
        private_key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    ).decode()

    timestamp = "1656000000"
    payload = b'[{"event":"open","sg_event_id":"evt-1"}]'
    signature = private_key.sign(timestamp.encode() + payload, ec.ECDSA(hashes.SHA256()))
    signature_b64 = base64.b64encode(signature).decode()

    assert email_event_ingest.verify_signature(payload, signature_b64, timestamp, public_key_b64)

    # Tampered body must fail verification.
    tampered = b'[{"event":"open","sg_event_id":"evt-EVIL"}]'
    assert not email_event_ingest.verify_signature(
        tampered, signature_b64, timestamp, public_key_b64
    )

    # Wrong public key must fail verification.
    other_key = ec.generate_private_key(ec.SECP256R1())
    other_public_b64 = base64.b64encode(
        other_key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    ).decode()
    assert not email_event_ingest.verify_signature(
        payload, signature_b64, timestamp, other_public_b64
    )


async def test_webhook_endpoint_rejects_bad_signature_when_key_configured(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.api.v1.endpoints import webhooks as webhooks_api
    from app.core.database import get_session
    from app.main import create_app

    private_key = ec.generate_private_key(ec.SECP256R1())
    public_key_b64 = base64.b64encode(
        private_key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    ).decode()
    monkeypatch.setattr(
        webhooks_api,
        "get_settings",
        lambda: Settings(sendgrid_webhook_public_key=public_key_b64),
    )

    app = create_app()

    async def _get_session():
        yield db_session

    app.dependency_overrides[get_session] = _get_session

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        res = await c.post(
            "/webhooks/sendgrid/events",
            content=b'[{"event":"open","sg_event_id":"x"}]',
            headers={
                "X-Twilio-Email-Event-Webhook-Signature": "bm90LXZhbGlk",
                "X-Twilio-Email-Event-Webhook-Timestamp": "1656000000",
            },
        )
    assert res.status_code == 401


async def test_webhook_endpoint_ingests_valid_signed_payload(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.api.v1.endpoints import webhooks as webhooks_api
    from app.core.database import get_session
    from app.main import create_app

    await _make_campaign(db_session)
    lead = await lead_service.capture_lead(db_session, "nog-2026", _lead())

    private_key = ec.generate_private_key(ec.SECP256R1())
    public_key_b64 = base64.b64encode(
        private_key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    ).decode()
    monkeypatch.setattr(
        webhooks_api,
        "get_settings",
        lambda: Settings(sendgrid_webhook_public_key=public_key_b64),
    )

    body = json.dumps([_open_event(lead.id, sg_event_id="evt-http")]).encode()
    timestamp = "1656000000"
    signature = private_key.sign(timestamp.encode() + body, ec.ECDSA(hashes.SHA256()))
    signature_b64 = base64.b64encode(signature).decode()

    app = create_app()

    async def _get_session():
        yield db_session

    app.dependency_overrides[get_session] = _get_session

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        res = await c.post(
            "/webhooks/sendgrid/events",
            content=body,
            headers={
                "X-Twilio-Email-Event-Webhook-Signature": signature_b64,
                "X-Twilio-Email-Event-Webhook-Timestamp": timestamp,
                "content-type": "application/json",
            },
        )
    assert res.status_code == 200
    assert res.json()["processed"] == "1"

    await db_session.refresh(lead)
    assert lead.pack_opened_count == 1
