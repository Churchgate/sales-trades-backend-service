import httpx
import pytest
import respx
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.endpoints import campaigns as campaigns_api
from app.core.config import Settings
from app.models.campaign import STATUS_ACTIVE, STATUS_DRAFT, Campaign
from app.models.lead import CRM_FAILED, CRM_PENDING, CRM_SKIPPED, CRM_SYNCED
from app.repositories import campaigns_repo, leads_repo
from app.schemas.campaigns import CampaignCreateRequest, LeadCreateRequest
from app.services import lead_crm_sync, lead_export, lead_service

_CONFIG = {
    "base_tags": ["Stand App", "NOG Energy Week 2026"],
    "tag_map": {"Office Leasing": "Office Leasing"},
    "inspection_tag": "Private Inspection",
    "newsletter_tag": "Newsletter Opt-In",
    "digital_pack_tag": "Digital Pack",
}


async def _make_campaign(
    session: AsyncSession, slug: str = "nog-2026", status: str = STATUS_ACTIVE
) -> Campaign:
    return await campaigns_repo.create(
        session,
        Campaign(slug=slug, name="Test Event", status=status, config=_CONFIG),
    )


def _lead(email: str = "ada@example.com", **overrides) -> LeadCreateRequest:
    data = {
        "first_name": "Ada",
        "last_name": "Lovelace",
        "email": email,
        "phone": "+2348012345678",
        "company": "Energy Co",
    }
    data.update(overrides)
    return LeadCreateRequest(**data)


# --- capture (service) ---


async def test_capture_lead_creates_and_derives_tags(db_session: AsyncSession) -> None:
    await _make_campaign(db_session)
    lead = await lead_service.capture_lead(
        db_session,
        "nog-2026",
        _lead(interests=["Office Leasing"], marketing_opt_in=True, consent_status=True),
    )
    assert lead.id is not None
    assert lead.email == "ada@example.com"
    assert lead.crm_sync_status == CRM_PENDING
    assert lead.consent_at is not None
    # base tags + interest tag + newsletter tag
    assert "Stand App" in lead.tags
    assert "Office Leasing" in lead.tags
    assert "Newsletter Opt-In" in lead.tags


async def test_capture_normalises_email_and_dedups(db_session: AsyncSession) -> None:
    await _make_campaign(db_session)
    first = await lead_service.capture_lead(db_session, "nog-2026", _lead(email="ADA@Example.com"))
    # Same email different case + updated company -> merges onto the same row.
    second = await lead_service.capture_lead(
        db_session, "nog-2026", _lead(email="ada@example.com", company="New Energy Co")
    )
    assert second.id == first.id
    assert second.company == "New Energy Co"
    rows = await leads_repo.list_for_campaign(db_session, first.campaign_id)
    assert len(rows) == 1


async def test_capture_unknown_campaign_raises(db_session: AsyncSession) -> None:
    with pytest.raises(lead_service.CampaignNotFoundError):
        await lead_service.capture_lead(db_session, "missing", _lead())


async def test_capture_inactive_campaign_raises(db_session: AsyncSession) -> None:
    await _make_campaign(db_session, slug="draft-event", status=STATUS_DRAFT)
    with pytest.raises(lead_service.CampaignInactiveError):
        await lead_service.capture_lead(db_session, "draft-event", _lead())


# --- capture (endpoint envelope + error mapping) ---


async def test_capture_endpoint_returns_envelope(db_session: AsyncSession) -> None:
    await _make_campaign(db_session)
    resp = await campaigns_api.capture_lead("nog-2026", _lead(), db_session)
    assert resp.status_code == 201
    assert resp.lead.email == "ada@example.com"


async def test_capture_endpoint_inactive_returns_409(db_session: AsyncSession) -> None:
    await _make_campaign(db_session, slug="draft-event", status=STATUS_DRAFT)
    with pytest.raises(HTTPException) as exc_info:
        await campaigns_api.capture_lead("draft-event", _lead(), db_session)
    assert exc_info.value.status_code == 409


async def test_get_campaign_public_returns_config(db_session: AsyncSession) -> None:
    await _make_campaign(db_session)
    resp = await campaigns_api.get_campaign("nog-2026", db_session)
    assert resp.campaign.slug == "nog-2026"
    assert resp.campaign.config["base_tags"] == ["Stand App", "NOG Energy Week 2026"]


async def test_get_campaign_unknown_404(db_session: AsyncSession) -> None:
    with pytest.raises(HTTPException) as exc_info:
        await campaigns_api.get_campaign("nope", db_session)
    assert exc_info.value.status_code == 404


# --- admin: create / stats / list / export ---


async def test_create_campaign_duplicate_slug_conflicts(db_session: AsyncSession) -> None:
    await campaigns_api.create_campaign(
        CampaignCreateRequest(slug="evt", name="Evt"), db_session
    )
    with pytest.raises(HTTPException) as exc_info:
        await campaigns_api.create_campaign(
            CampaignCreateRequest(slug="evt", name="Evt 2"), db_session
        )
    assert exc_info.value.status_code == 409


async def test_campaign_stats_counts(db_session: AsyncSession) -> None:
    campaign = await _make_campaign(db_session)
    await lead_service.capture_lead(
        db_session, "nog-2026",
        _lead(email="a@example.com", interests=["Office Leasing"], inspection_requested=True),
    )
    await lead_service.capture_lead(
        db_session, "nog-2026",
        _lead(email="b@example.com", interests=["Office Leasing", "Clubhouse"],
              marketing_opt_in=True, source="tablet"),
    )
    resp = await campaigns_api.campaign_stats(campaign.id, db_session)
    stats = resp.stats
    assert stats.total_leads == 2
    assert stats.inspection_requests == 1
    assert stats.marketing_opt_ins == 1
    assert stats.by_interest["Office Leasing"] == 2
    assert stats.by_interest["Clubhouse"] == 1
    assert stats.by_source == {"qr": 1, "tablet": 1}
    assert sum(d.count for d in stats.by_day) == 2
    # sync disabled by default -> nothing synced yet
    assert stats.synced_count == 0
    assert stats.unsynced_count == 2


async def test_list_leads_filters_by_interest(db_session: AsyncSession) -> None:
    campaign = await _make_campaign(db_session)
    await lead_service.capture_lead(
        db_session, "nog-2026", _lead(email="a@example.com", interests=["Office Leasing"])
    )
    await lead_service.capture_lead(
        db_session, "nog-2026", _lead(email="b@example.com", interests=["Clubhouse"])
    )
    resp = await campaigns_api.list_leads(campaign.id, db_session, interest="Office Leasing")
    assert resp.total == 1
    assert resp.leads[0].email == "a@example.com"


async def test_export_csv_contains_header_and_rows(db_session: AsyncSession) -> None:
    campaign = await _make_campaign(db_session)
    await lead_service.capture_lead(db_session, "nog-2026", _lead(email="a@example.com"))
    resp = await campaigns_api.export_leads_csv(campaign.id, db_session)
    body = resp.body.decode()
    assert resp.media_type == "text/csv"
    assert "email" in body.splitlines()[0]
    assert "a@example.com" in body


async def test_leads_to_csv_formats_arrays(db_session: AsyncSession) -> None:
    await _make_campaign(db_session)
    lead = await lead_service.capture_lead(
        db_session, "nog-2026", _lead(interests=["Office Leasing", "Clubhouse"])
    )
    csv_text = lead_export.leads_to_csv([lead])
    assert "Office Leasing; Clubhouse" in csv_text


# --- CRM sync ---


async def test_sync_lead_skipped_when_disabled(db_session: AsyncSession) -> None:
    campaign = await _make_campaign(db_session)
    lead = await lead_service.capture_lead(db_session, "nog-2026", _lead())
    disabled = Settings(freshsales_lead_sync_enabled=False)
    from app.freshsales.client import FreshsalesClient

    async with FreshsalesClient(disabled) as client:
        result = await lead_crm_sync.sync_lead(
            db_session, lead, campaign, client=client, settings=disabled
        )
    assert result.crm_sync_status == CRM_SKIPPED


async def test_sync_lead_success_marks_synced(db_session: AsyncSession) -> None:
    campaign = await _make_campaign(db_session)
    lead = await lead_service.capture_lead(db_session, "nog-2026", _lead())
    enabled = Settings(freshsales_lead_sync_enabled=True, freshsales_api_key="SG.test")
    from app.freshsales.client import FreshsalesClient

    with respx.mock(base_url=enabled.freshsales_base_url) as router:
        router.post("/crm/sales/api/contacts/upsert").mock(
            return_value=httpx.Response(200, json={"contact": {"id": 4242}})
        )
        async with FreshsalesClient(enabled) as client:
            result = await lead_crm_sync.sync_lead(
                db_session, lead, campaign, client=client, settings=enabled
            )
    assert result.crm_sync_status == CRM_SYNCED
    assert result.crm_contact_id == "4242"
    assert result.crm_synced_at is not None


async def test_sync_lead_failure_marks_failed(db_session: AsyncSession) -> None:
    campaign = await _make_campaign(db_session)
    lead = await lead_service.capture_lead(db_session, "nog-2026", _lead())
    enabled = Settings(freshsales_lead_sync_enabled=True, freshsales_api_key="SG.test")
    from app.freshsales.client import FreshsalesClient

    with respx.mock(base_url=enabled.freshsales_base_url) as router:
        # 400 is non-retryable -> fails fast (no backoff sleeps).
        router.post("/crm/sales/api/contacts/upsert").mock(
            return_value=httpx.Response(400, json={"errors": "bad"})
        )
        async with FreshsalesClient(enabled) as client:
            result = await lead_crm_sync.sync_lead(
                db_session, lead, campaign, client=client, settings=enabled
            )
    assert result.crm_sync_status == CRM_FAILED
    assert result.crm_error is not None


async def test_sync_pending_pushes_pending_leads(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _make_campaign(db_session)
    await lead_service.capture_lead(db_session, "nog-2026", _lead(email="a@example.com"))
    await lead_service.capture_lead(db_session, "nog-2026", _lead(email="b@example.com"))
    enabled = Settings(freshsales_lead_sync_enabled=True, freshsales_api_key="SG.test")
    monkeypatch.setattr(lead_crm_sync, "get_settings", lambda: enabled)

    with respx.mock(base_url=enabled.freshsales_base_url) as router:
        router.post("/crm/sales/api/contacts/upsert").mock(
            return_value=httpx.Response(200, json={"contact": {"id": 1}})
        )
        synced = await lead_crm_sync.sync_pending(db_session)
    assert synced == 2
