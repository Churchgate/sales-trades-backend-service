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
    "materials": [
        "Corporate Prospectus",
        "Office Floorplates",
        "Residence Floorplans",
    ],
    "materials_assets": {
        # Bare string: single-file material (normalised to a 1-item list).
        "Corporate Prospectus": "https://assets.example.com/prospectus.pdf",
        # List: multi-file material (e.g. several floorplate images).
        "Office Floorplates": [
            "https://assets.example.com/office-1.png",
            "https://assets.example.com/office-2.png",
        ],
        # Residence Floorplans intentionally has no asset (tests the gap path).
    },
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


async def test_capture_survives_concurrent_duplicate_insert(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two retries racing the dedup SELECT both attempt INSERT; the unique
    (campaign_id, email) index makes the second one fail. capture_lead should
    recover by merging onto the row the other request created, not 500."""
    campaign = await _make_campaign(db_session)
    first = await lead_service.capture_lead(db_session, "nog-2026", _lead())

    # Force capture_lead's dedup SELECT to miss once, as if this request's
    # snapshot was taken before the other request's row was visible — the
    # row is already committed in the DB, so the INSERT below hits the
    # unique (campaign_id, email) violation.
    real_lookup = leads_repo.get_by_campaign_email
    calls = {"n": 0}

    async def _miss_once(session: AsyncSession, campaign_id: int, email: str):
        calls["n"] += 1
        if calls["n"] == 1:
            return None
        return await real_lookup(session, campaign_id, email)

    monkeypatch.setattr(lead_service.leads_repo, "get_by_campaign_email", _miss_once)

    second = await lead_service.capture_lead(
        db_session, "nog-2026", _lead(company="Retried Co")
    )
    assert second.id == first.id
    assert second.company == "Retried Co"
    rows = await leads_repo.list_for_campaign(db_session, campaign.id)
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
    assert stats.packs_delivered == 0  # no email configured in tests
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


# --- digital-pack delivery ---


async def test_deliver_pack_sends_only_materials_with_assets(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.models.lead import PACK_SENT
    from app.services import pack_delivery

    campaign = await _make_campaign(db_session)
    lead = await lead_service.capture_lead(
        db_session, "nog-2026",
        _lead(requested_materials=["Corporate Prospectus", "Residence Floorplans"]),
    )
    assert lead.pack_delivery_status == "pending"

    sent: dict = {}

    async def _fake_send(
        *, to_email, subject, html, text, settings=None, from_email=None, from_name=None,
    ):
        sent.update(to_email=to_email, subject=subject, html=html, text=text,
                     from_email=from_email, from_name=from_name)
        return True

    enabled = Settings(sendgrid_api_key="SG.test")
    monkeypatch.setattr(pack_delivery.mailer, "send_email", _fake_send)

    result = await pack_delivery.deliver_pack(db_session, lead, campaign, settings=enabled)
    assert result.pack_delivery_status == PACK_SENT
    # Only the material with a configured asset is delivered (Residence has none).
    assert result.pack_delivered_materials == ["Corporate Prospectus"]
    assert "Corporate Prospectus" in sent["html"]
    assert "Residence Floorplans" not in sent["html"]
    # Event emails use their own sender identity, not bookings' no-reply address.
    assert sent["from_email"] == enabled.event_mail_from_email
    assert sent["from_name"] == enabled.event_mail_from_name


async def test_deliver_pack_sends_every_file_for_a_multi_file_material(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Office Floorplates has two images configured — both must be linked,
    each labelled distinctly so the visitor can tell them apart."""
    from app.services import pack_delivery

    campaign = await _make_campaign(db_session)
    lead = await lead_service.capture_lead(
        db_session, "nog-2026", _lead(requested_materials=["Office Floorplates"])
    )

    sent: dict = {}

    async def _fake_send(
        *, to_email, subject, html, text, settings=None, from_email=None, from_name=None,
    ):
        sent.update(html=html, text=text)
        return True

    enabled = Settings(sendgrid_api_key="SG.test")
    monkeypatch.setattr(pack_delivery.mailer, "send_email", _fake_send)

    result = await pack_delivery.deliver_pack(db_session, lead, campaign, settings=enabled)
    assert result.pack_delivered_materials == ["Office Floorplates"]
    assert "https://assets.example.com/office-1.png" in sent["html"]
    assert "https://assets.example.com/office-2.png" in sent["html"]
    assert "Office Floorplates" in sent["html"]
    assert "Download 1" in sent["html"]
    assert "Download 2" in sent["html"]


async def test_deliver_pack_includes_contact_info_when_configured(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Contact email/phone are optional, set later via campaign config — when
    present they must show up; when absent (today's reality) no broken/empty
    'Questions?' section should render."""
    from app.services import pack_delivery

    campaign = await _make_campaign(db_session)
    lead_no_contact = await lead_service.capture_lead(
        db_session, "nog-2026",
        _lead(email="a@example.com", requested_materials=["Corporate Prospectus"]),
    )

    async def _fake_send(
        *, to_email, subject, html, text, settings=None, from_email=None, from_name=None,
    ):
        _fake_send.last = {"html": html, "text": text}
        return True

    enabled = Settings(sendgrid_api_key="SG.test")
    monkeypatch.setattr(pack_delivery.mailer, "send_email", _fake_send)

    await pack_delivery.deliver_pack(db_session, lead_no_contact, campaign, settings=enabled)
    assert "Questions?" not in _fake_send.last["html"]

    campaign.config = {
        **campaign.config,
        "digital_pack": {
            "contact_email": "events@wtcabuja.com",
            "contact_phone": "+234 800 000 0000",
        },
    }
    lead_with_contact = await lead_service.capture_lead(
        db_session, "nog-2026",
        _lead(email="b@example.com", requested_materials=["Corporate Prospectus"]),
    )
    await pack_delivery.deliver_pack(db_session, lead_with_contact, campaign, settings=enabled)
    assert "events@wtcabuja.com" in _fake_send.last["html"]
    assert "+234 800 000 0000" in _fake_send.last["html"]


async def test_deliver_pack_includes_logo_when_configured(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Logo is optional, set later via campaign config — when present it must
    render as an <img>; when absent, no broken image tag should appear."""
    from app.services import pack_delivery

    campaign = await _make_campaign(db_session)
    lead_no_logo = await lead_service.capture_lead(
        db_session, "nog-2026",
        _lead(email="a@example.com", requested_materials=["Corporate Prospectus"]),
    )

    async def _fake_send(
        *, to_email, subject, html, text, settings=None, from_email=None, from_name=None,
    ):
        _fake_send.last = {"html": html, "text": text}
        return True

    enabled = Settings(sendgrid_api_key="SG.test")
    monkeypatch.setattr(pack_delivery.mailer, "send_email", _fake_send)

    await pack_delivery.deliver_pack(db_session, lead_no_logo, campaign, settings=enabled)
    assert "<img" not in _fake_send.last["html"]

    logo_url = "https://assets.example.com/wtc-logo.png"
    campaign.config = {**campaign.config, "digital_pack": {"logo_url": logo_url}}
    lead_with_logo = await lead_service.capture_lead(
        db_session, "nog-2026",
        _lead(email="b@example.com", requested_materials=["Corporate Prospectus"]),
    )
    await pack_delivery.deliver_pack(db_session, lead_with_logo, campaign, settings=enabled)
    assert f'<img src="{logo_url}"' in _fake_send.last["html"]


async def test_deliver_pack_skipped_when_email_unconfigured(db_session: AsyncSession) -> None:
    from app.services import pack_delivery

    campaign = await _make_campaign(db_session)
    lead = await lead_service.capture_lead(
        db_session, "nog-2026", _lead(requested_materials=["Corporate Prospectus"])
    )
    disabled = Settings(sendgrid_api_key="")
    result = await pack_delivery.deliver_pack(db_session, lead, campaign, settings=disabled)
    assert result.pack_delivery_status == "skipped"


async def test_deliver_pack_newsletter_pseudo_item_is_not_a_delivery(
    db_session: AsyncSession,
) -> None:
    """The 'WTC Abuja Updates & Private Invitations' checkbox is the newsletter
    opt-in, not a document — it must never count as a deliverable material."""
    from app.services import pack_delivery

    campaign = await _make_campaign(db_session)
    lead = await lead_service.capture_lead(
        db_session, "nog-2026",
        _lead(requested_materials=["WTC Abuja Updates & Private Invitations"]),
    )
    enabled = Settings(sendgrid_api_key="SG.test")
    result = await pack_delivery.deliver_pack(db_session, lead, campaign, settings=enabled)
    assert result.pack_delivery_status == "not_requested"
    assert result.pack_delivered_materials is None


async def test_deliver_pending_picks_up_pending_packs(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import pack_delivery

    await _make_campaign(db_session)
    await lead_service.capture_lead(
        db_session, "nog-2026",
        _lead(email="a@example.com", requested_materials=["Corporate Prospectus"]),
    )
    await lead_service.capture_lead(
        db_session, "nog-2026",
        _lead(email="b@example.com", requested_materials=["Office Floorplates"]),
    )
    enabled = Settings(sendgrid_api_key="SG.test")
    monkeypatch.setattr(pack_delivery, "get_settings", lambda: enabled)

    async def _fake_send(
        *, to_email, subject, html, text, settings=None, from_email=None, from_name=None,
    ):
        return True

    monkeypatch.setattr(pack_delivery.mailer, "send_email", _fake_send)

    delivered = await pack_delivery.deliver_pending(db_session)
    assert delivered == 2


async def test_stats_by_material_counts_requests(db_session: AsyncSession) -> None:
    campaign = await _make_campaign(db_session)
    await lead_service.capture_lead(
        db_session, "nog-2026",
        _lead(email="a@example.com", requested_materials=["Corporate Prospectus"]),
    )
    await lead_service.capture_lead(
        db_session, "nog-2026",
        _lead(email="b@example.com",
              requested_materials=["Corporate Prospectus", "Office Floorplates"]),
    )
    resp = await campaigns_api.campaign_stats(campaign.id, db_session)
    assert resp.stats.by_material["Corporate Prospectus"] == 2
    assert resp.stats.by_material["Office Floorplates"] == 1


async def test_engagement_score_ranks_by_intent(db_session: AsyncSession) -> None:
    from app.services import lead_scoring

    await _make_campaign(db_session)
    hot = await lead_service.capture_lead(
        db_session, "nog-2026",
        _lead(email="hot@example.com", interests=["Office Leasing"],
              requested_materials=["Corporate Prospectus"],
              inspection_requested=True, timing="Immediately", marketing_opt_in=True),
    )
    cold = await lead_service.capture_lead(
        db_session, "nog-2026",
        _lead(email="cold@example.com", timing="Researching"),
    )
    assert lead_scoring.engagement_score(hot) > lead_scoring.engagement_score(cold)
    # Inspection + interest + material + immediate timing should score well.
    assert lead_scoring.engagement_score(hot) >= 50


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
