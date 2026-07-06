import httpx
import pytest
import respx
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.endpoints import campaigns as campaigns_api
from app.core.config import Settings
from app.models.campaign import STATUS_ACTIVE, STATUS_DRAFT, Campaign
from app.models.lead import CRM_FAILED, CRM_PENDING, CRM_SKIPPED, CRM_SYNCED, Lead
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


async def test_capture_accepts_blank_or_missing_phone(db_session: AsyncSession) -> None:
    """The public website marks phone optional; a blank/omitted phone must not be
    rejected and must still capture (stored as "") so the pack can be delivered.
    Regression: phone was required (min_length=1), silently 422'ing every phone-less
    website registration before a lead was ever created."""
    await _make_campaign(db_session)
    # phone omitted entirely -> schema default None (would previously fail validation).
    payload = LeadCreateRequest(
        first_name="Ada", last_name="Lovelace", email="nophone@example.com",
        company="Energy Co",
    )
    assert payload.phone is None
    lead = await lead_service.capture_lead(db_session, "nog-2026", payload)
    assert lead.id is not None
    assert lead.phone == ""  # NOT NULL column, coerced from None
    # An explicit empty string (what the website form actually sends) is accepted too.
    lead2 = await lead_service.capture_lead(
        db_session, "nog-2026",
        LeadCreateRequest(first_name="Grace", last_name="Hopper",
                          email="blank@example.com", phone="", company="Navy"),
    )
    assert lead2.phone == ""


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


async def test_delete_lead_removes_it(db_session: AsyncSession) -> None:
    campaign = await _make_campaign(db_session)
    lead = await lead_service.capture_lead(db_session, "nog-2026", _lead())
    resp = await campaigns_api.delete_lead(campaign.id, lead.id, db_session)
    assert resp.status_code == 200
    rows = await leads_repo.list_for_campaign(db_session, campaign.id)
    assert rows == []


async def test_delete_lead_unknown_404(db_session: AsyncSession) -> None:
    campaign = await _make_campaign(db_session)
    with pytest.raises(HTTPException) as exc_info:
        await campaigns_api.delete_lead(campaign.id, 999_999, db_session)
    assert exc_info.value.status_code == 404


async def test_delete_lead_wrong_campaign_404(db_session: AsyncSession) -> None:
    """A lead_id that exists but belongs to a different campaign must 404, not
    leak/delete cross-campaign."""
    campaign_a = await _make_campaign(db_session, slug="event-a")
    await _make_campaign(db_session, slug="event-b")
    lead = await lead_service.capture_lead(db_session, "event-a", _lead())
    other_campaign_id = (await campaigns_repo.get_by_slug(db_session, "event-b")).id
    with pytest.raises(HTTPException) as exc_info:
        await campaigns_api.delete_lead(other_campaign_id, lead.id, db_session)
    assert exc_info.value.status_code == 404
    # Untouched — still present under its real campaign.
    rows = await leads_repo.list_for_campaign(db_session, campaign_a.id)
    assert len(rows) == 1


async def test_delete_lead_requires_superadmin_over_http(db_session: AsyncSession) -> None:
    """Driven over real HTTP (not a direct function call) so require_role("superadmin")
    is actually exercised — admin (not superadmin) must be forbidden."""
    import httpx

    from app.api.dependencies import get_current_user
    from app.core.database import get_session
    from app.main import create_app
    from app.models.dashboard_user import DashboardUser

    campaign = await _make_campaign(db_session)
    lead = await lead_service.capture_lead(db_session, "nog-2026", _lead())

    app = create_app()

    async def _get_session():
        yield db_session

    app.dependency_overrides[get_session] = _get_session

    def _client_as(role: str) -> httpx.AsyncClient:
        user = DashboardUser(email="staff@churchgate.com", role=role, owner_id=None,
                              hashed_password="x")
        app.dependency_overrides[get_current_user] = lambda: user
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")

    async with _client_as("admin") as c:
        res = await c.delete(f"/api/v1/campaigns/{campaign.id}/leads/{lead.id}")
    assert res.status_code == 403

    async with _client_as("superadmin") as c:
        res = await c.delete(f"/api/v1/campaigns/{campaign.id}/leads/{lead.id}")
    assert res.status_code == 200

    rows = await leads_repo.list_for_campaign(db_session, campaign.id)
    assert rows == []


async def test_bulk_delete_leads_no_filters_purges_campaign(db_session: AsyncSession) -> None:
    campaign = await _make_campaign(db_session)
    await lead_service.capture_lead(db_session, "nog-2026", _lead(email="a@example.com"))
    await lead_service.capture_lead(db_session, "nog-2026", _lead(email="b@example.com"))
    resp = await campaigns_api.bulk_delete_leads(campaign.id, db_session, confirm=True)
    assert resp.status_code == 200
    assert resp.message == "Deleted 2 lead(s)"
    rows = await leads_repo.list_for_campaign(db_session, campaign.id)
    assert rows == []


async def test_bulk_delete_leads_respects_filters(db_session: AsyncSession) -> None:
    """Same filters as GET .../leads — only matching leads are removed."""
    campaign = await _make_campaign(db_session)
    await lead_service.capture_lead(
        db_session, "nog-2026", _lead(email="a@example.com", interests=["Office Leasing"])
    )
    await lead_service.capture_lead(
        db_session, "nog-2026", _lead(email="b@example.com", interests=["Clubhouse"])
    )
    resp = await campaigns_api.bulk_delete_leads(
        campaign.id, db_session, confirm=True, interest="Office Leasing"
    )
    assert resp.message == "Deleted 1 lead(s)"
    rows = await leads_repo.list_for_campaign(db_session, campaign.id)
    assert len(rows) == 1
    assert rows[0].email == "b@example.com"


async def test_bulk_delete_leads_requires_confirm(db_session: AsyncSession) -> None:
    """confirm=true is required on every call, not just role — the default
    (no filters, no confirm) would otherwise silently wipe a whole campaign."""
    campaign = await _make_campaign(db_session)
    await lead_service.capture_lead(db_session, "nog-2026", _lead())
    with pytest.raises(HTTPException) as exc_info:
        await campaigns_api.bulk_delete_leads(campaign.id, db_session, confirm=False)
    assert exc_info.value.status_code == 400
    rows = await leads_repo.list_for_campaign(db_session, campaign.id)
    assert len(rows) == 1


async def test_bulk_delete_leads_requires_superadmin_over_http(db_session: AsyncSession) -> None:
    import httpx

    from app.api.dependencies import get_current_user
    from app.core.database import get_session
    from app.main import create_app
    from app.models.dashboard_user import DashboardUser

    campaign = await _make_campaign(db_session)
    await lead_service.capture_lead(db_session, "nog-2026", _lead())

    app = create_app()

    async def _get_session():
        yield db_session

    app.dependency_overrides[get_session] = _get_session

    def _client_as(role: str) -> httpx.AsyncClient:
        user = DashboardUser(email="staff@churchgate.com", role=role, owner_id=None,
                              hashed_password="x")
        app.dependency_overrides[get_current_user] = lambda: user
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")

    async with _client_as("admin") as c:
        res = await c.delete(f"/api/v1/campaigns/{campaign.id}/leads?confirm=true")
    assert res.status_code == 403

    async with _client_as("superadmin") as c:
        res = await c.delete(f"/api/v1/campaigns/{campaign.id}/leads?confirm=true")
    assert res.status_code == 200

    rows = await leads_repo.list_for_campaign(db_session, campaign.id)
    assert rows == []


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
        *, to_email, subject, html, text, settings=None, from_email=None, from_name=None, cc=None,
    ):
        sent.update(to_email=to_email, subject=subject, html=html, text=text,
                     from_email=from_email, from_name=from_name)
        return True

    enabled = Settings(wtc_sendgrid_api_key="SG.wtc.test")
    monkeypatch.setattr(pack_delivery.campaign_mailer, "send_campaign_email", _fake_send)

    result = await pack_delivery.deliver_pack(db_session, lead, campaign, settings=enabled)
    assert result.pack_delivery_status == PACK_SENT
    # Only the material with a configured asset is delivered (Residence has none).
    assert result.pack_delivered_materials == ["Corporate Prospectus"]
    assert "Corporate Prospectus" in sent["html"]
    assert "Residence Floorplans" not in sent["html"]


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
        *, to_email, subject, html, text, settings=None, from_email=None, from_name=None, cc=None,
    ):
        sent.update(html=html, text=text)
        return True

    enabled = Settings(wtc_sendgrid_api_key="SG.wtc.test")
    monkeypatch.setattr(pack_delivery.campaign_mailer, "send_campaign_email", _fake_send)

    result = await pack_delivery.deliver_pack(db_session, lead, campaign, settings=enabled)
    assert result.pack_delivered_materials == ["Office Floorplates"]
    assert "https://assets.example.com/office-1.png" in sent["html"]
    assert "https://assets.example.com/office-2.png" in sent["html"]
    assert "Office Floorplates" in sent["html"]
    assert "Download 1" in sent["html"]
    assert "Download 2" in sent["html"]


async def test_pack_email_is_source_aware_and_uses_display_meta(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pack email renders per-material display titles, always carries the enquiries
    contact, and only shows the event line when the campaign config sets one."""
    from app.services import pack_delivery

    campaign = await _make_campaign(db_session)
    campaign.config = {
        **campaign.config,
        "digital_pack": {"event_line": "NOG Energy Week 2026 &middot; 5–9 July 2026"},
        "materials_display": {
            "Corporate Prospectus": {"title": "Full Development Brochure", "featured": True},
        },
    }

    async def _fake_send(
        *, to_email, subject, html, text, settings=None, from_email=None, from_name=None, cc=None,
    ):
        _fake_send.last = {"html": html, "text": text}
        return True

    enabled = Settings(wtc_sendgrid_api_key="SG.wtc.test")
    monkeypatch.setattr(pack_delivery.campaign_mailer, "send_campaign_email", _fake_send)

    lead = await lead_service.capture_lead(
        db_session, "nog-2026",
        _lead(email="a@example.com", requested_materials=["Corporate Prospectus"]),
    )
    await pack_delivery.deliver_pack(db_session, lead, campaign, settings=enabled)
    html = _fake_send.last["html"]
    assert "Full Development Brochure" in html  # display title, not the raw label
    assert "enquiries@wtcabuja.com" in html  # fixed contact strip
    assert "NOG Energy Week 2026" in html  # event line present when configured
    # Mobile-responsive download button: the media query stacks the row and the
    # nowrap keeps the label from wrapping letter-by-letter on a collapsed column.
    assert "@media only screen and (max-width:480px)" in html
    assert "white-space:nowrap" in html
    assert 'class="wtc-btn"' in html and 'class="wtc-row-btn"' in html
    # No forbidden CSS that email clients strip.
    for bad in ("position:absolute", "display:flex", "object-fit", "linear-gradient"):
        assert bad not in html

    # A campaign without an event_line omits it.
    campaign.config = {**campaign.config, "digital_pack": {}}
    lead2 = await lead_service.capture_lead(
        db_session, "nog-2026",
        _lead(email="b@example.com", requested_materials=["Corporate Prospectus"]),
    )
    await pack_delivery.deliver_pack(db_session, lead2, campaign, settings=enabled)
    assert "NOG Energy Week 2026" not in _fake_send.last["html"]


async def test_build_viewing_booking_email() -> None:
    """The viewing-confirmation email greets by name, links the brochure when
    configured, and stays email-client-safe."""
    from app.services import campaign_mailer

    campaign = Campaign(
        slug="nog-2026", name="Test Event", status=STATUS_ACTIVE,
        config={
            "viewing_booking": {"brochure_url": "https://assets.example.com/brochure.pdf"},
            "digital_pack": {"hero_url": "https://h/hero.jpg", "logo_url": "https://l/logo.png"},
        },
    )
    lead = Lead(campaign_id=1, email="v@example.com", first_name="Ada", last_name="L",
                phone="+234", company="Energy Co", inspection_requested=True)
    subject, html, text = campaign_mailer.build_viewing_booking_email(lead, campaign)
    assert "Viewing Request" in subject
    assert "Hello Ada," in html and "Hello Ada," in text
    assert "https://assets.example.com/brochure.pdf" in html
    assert "https://h/hero.jpg" in html and "https://l/logo.png" in html
    for bad in ("position:absolute", "display:flex", "object-fit", "linear-gradient"):
        assert bad not in html


async def test_viewing_email_includes_full_document_set() -> None:
    """Viewing registrants don't pick materials, so the viewing email carries the
    full document set (brochure + both floorplans), not just a brochure teaser."""
    from app.services import campaign_mailer

    campaign = Campaign(
        slug="wtcabuja-website", name="Web", status=STATUS_ACTIVE,
        config={
            "materials": ["brochure", "office_floorplans", "residential_plans"],
            "materials_assets": {
                "brochure": "https://a/brochure.pdf",
                "office_floorplans": ["https://a/office.pdf"],
                "residential_plans": "https://a/residential.pdf",
            },
            "materials_display": {
                "brochure": {"title": "Full Development Brochure", "featured": True},
                "office_floorplans": {"eyebrow": "Office Floorplate", "title": "Grade A Offices"},
                "residential_plans": {"title": "Executive Residences"},
            },
            "viewing_booking": {"brochure_url": "https://a/brochure.pdf"},
            "digital_pack": {"hero_url": "https://h/hero.jpg", "logo_url": "https://l/logo.png"},
        },
    )
    lead = Lead(campaign_id=2, email="v@example.com", first_name="Ada", last_name="L",
                phone="", company="Co", inspection_requested=True)
    _subject, html, text = campaign_mailer.build_viewing_booking_email(lead, campaign)
    for url in ("https://a/brochure.pdf", "https://a/office.pdf", "https://a/residential.pdf"):
        assert url in html and url in text  # floorplans present in both parts
    assert "Grade A Offices" in html and "Executive Residences" in html
    assert "Before your visit" in html


async def test_capture_with_inspection_sends_viewing_email(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A lead flagged inspection_requested triggers the viewing-confirmation email
    over the capture endpoint; a pack-only lead does not."""
    from app.api.v1.endpoints import campaigns as campaigns_api

    await _make_campaign(db_session)
    sent: list = []

    async def _fake_view(lead, campaign, settings=None):
        sent.append(lead.email)
        return True

    async def _noop_notify(lead, campaign, settings=None):
        return None

    monkeypatch.setattr(campaigns_api.campaign_mailer, "send_viewing_booking", _fake_view)
    monkeypatch.setattr(campaigns_api.campaign_mailer, "send_lead_notification", _noop_notify)

    resp = await campaigns_api.capture_lead(
        "nog-2026", _lead(email="viewer@example.com", inspection_requested=True), db_session
    )
    assert sent == ["viewer@example.com"]
    # A successful viewing email carried the documents -> recorded as a pack delivery
    # so the dashboard shows "sent" rather than a bare "not requested".
    assert resp.lead.pack_delivery_status == "sent"
    assert "Corporate Prospectus" in (resp.lead.pack_delivered_materials or [])

    resp2 = await campaigns_api.capture_lead(
        "nog-2026", _lead(email="packer@example.com", inspection_requested=False), db_session
    )
    assert sent == ["viewer@example.com"]  # unchanged — no viewing email for pack-only
    assert resp2.lead.pack_delivery_status == "not_requested"  # untouched


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
        *, to_email, subject, html, text, settings=None, from_email=None, from_name=None, cc=None,
    ):
        _fake_send.last = {"html": html, "text": text}
        return True

    enabled = Settings(wtc_sendgrid_api_key="SG.wtc.test")
    monkeypatch.setattr(pack_delivery.campaign_mailer, "send_campaign_email", _fake_send)

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
    disabled = Settings(wtc_sendgrid_api_key="")
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
    enabled = Settings(wtc_sendgrid_api_key="SG.wtc.test")
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
    enabled = Settings(wtc_sendgrid_api_key="SG.wtc.test")
    monkeypatch.setattr(pack_delivery, "get_settings", lambda: enabled)

    async def _fake_send(
        *, to_email, subject, html, text, settings=None, from_email=None, from_name=None, cc=None,
    ):
        return True

    monkeypatch.setattr(pack_delivery.campaign_mailer, "send_campaign_email", _fake_send)

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


async def test_deliver_pack_ccs_configured_address(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When campaign_cc_email is set, the pack email CCs it (audit copy); when the
    CC equals the recipient it's dropped (SendGrid rejects a duplicate)."""
    from app.services import pack_delivery

    campaign = await _make_campaign(db_session)
    lead = await lead_service.capture_lead(
        db_session, "nog-2026",
        _lead(email="visitor@example.com", requested_materials=["Corporate Prospectus"]),
    )

    seen: dict = {}

    async def _fake_send(
        *, to_email, subject, html, text, settings=None, from_email=None, from_name=None, cc=None,
    ):
        seen["cc"] = cc
        return True

    monkeypatch.setattr(pack_delivery.campaign_mailer, "send_campaign_email", _fake_send)

    enabled = Settings(
        wtc_sendgrid_api_key="SG.wtc.test", campaign_cc_email="enquiries@wtcabuja.com"
    )
    await pack_delivery.deliver_pack(db_session, lead, campaign, settings=enabled)
    assert seen["cc"] == ["enquiries@wtcabuja.com"]

    no_cc = Settings(wtc_sendgrid_api_key="SG.wtc.test")
    await pack_delivery.deliver_pack(db_session, lead, campaign, settings=no_cc)
    assert seen["cc"] is None


async def test_send_campaign_email_drops_cc_that_equals_recipient(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A CC duplicating the To address is stripped from the SendGrid payload."""
    from app.services import campaign_mailer

    captured: dict = {}

    class _Resp:
        def raise_for_status(self) -> None:  # noqa: D401
            return None

    class _Client:
        def __init__(self, *a, **k) -> None: ...
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a) -> None:
            return None
        async def post(self, url, json=None, headers=None):
            captured["json"] = json
            return _Resp()

    monkeypatch.setattr(campaign_mailer.httpx, "AsyncClient", _Client)
    enabled = Settings(wtc_sendgrid_api_key="SG.wtc.test", event_mail_from_email="from@wtc.com")

    ok = await campaign_mailer.send_campaign_email(
        to_email="dup@example.com", subject="s", html="<p>h</p>", text="t",
        settings=enabled, cc=["DUP@example.com"],
    )
    assert ok is True
    assert "cc" not in captured["json"]["personalizations"][0]


async def test_resend_lead_pack_redelivers_over_http(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Per-lead resend endpoint re-attempts delivery and is allowed for admin
    (not only superadmin)."""
    from app.api.dependencies import get_current_user
    from app.core.database import get_session
    from app.main import create_app
    from app.models.dashboard_user import DashboardUser
    from app.services import pack_delivery

    campaign = await _make_campaign(db_session)
    lead = await lead_service.capture_lead(
        db_session, "nog-2026", _lead(requested_materials=["Corporate Prospectus"])
    )

    sends: list = []

    async def _fake_send(
        *, to_email, subject, html, text, settings=None, from_email=None, from_name=None, cc=None,
    ):
        sends.append(to_email)
        return True

    monkeypatch.setattr(pack_delivery.campaign_mailer, "send_campaign_email", _fake_send)
    monkeypatch.setattr(
        pack_delivery, "get_settings", lambda: Settings(wtc_sendgrid_api_key="SG.wtc.test")
    )

    app = create_app()

    async def _get_session():
        yield db_session

    app.dependency_overrides[get_session] = _get_session
    user = DashboardUser(
        email="staff@churchgate.com", role="admin", owner_id=None, hashed_password="x"
    )
    app.dependency_overrides[get_current_user] = lambda: user

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        res = await c.post(f"/api/v1/campaigns/{campaign.id}/leads/{lead.id}/resend-pack")
    assert res.status_code == 200, res.text
    assert res.json()["lead"]["pack_delivery_status"] == "sent"
    assert sends == ["ada@example.com"]


async def test_resend_viewing_lead_resends_viewing_email(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Per-lead resend on a viewing/enquiry lead (no requested materials) re-sends the
    viewing email (which carries the documents) and keeps the lead marked sent."""
    from app.api.dependencies import get_current_user
    from app.core.database import get_session
    from app.main import create_app
    from app.models.dashboard_user import DashboardUser
    from app.services import pack_delivery

    campaign = await _make_campaign(db_session)
    lead = await lead_service.capture_lead(
        db_session, "nog-2026",
        _lead(email="viewer@example.com", inspection_requested=True),
    )
    assert not lead.requested_materials  # viewing-only

    sends: list = []

    async def _fake_send(
        *, to_email, subject, html, text, settings=None, from_email=None, from_name=None, cc=None,
    ):
        sends.append(to_email)
        return True

    monkeypatch.setattr(pack_delivery.campaign_mailer, "send_campaign_email", _fake_send)
    monkeypatch.setattr(
        pack_delivery, "get_settings", lambda: Settings(wtc_sendgrid_api_key="SG.wtc.test")
    )

    app = create_app()

    async def _get_session():
        yield db_session

    app.dependency_overrides[get_session] = _get_session
    user = DashboardUser(
        email="staff@churchgate.com", role="admin", owner_id=None, hashed_password="x"
    )
    app.dependency_overrides[get_current_user] = lambda: user

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        res = await c.post(f"/api/v1/campaigns/{campaign.id}/leads/{lead.id}/resend-pack")
    assert res.status_code == 200, res.text
    assert res.json()["lead"]["pack_delivery_status"] == "sent"
    assert sends == ["viewer@example.com"]  # the viewing email was re-sent


async def test_resend_lead_pack_wrong_campaign_404(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.api.dependencies import get_current_user
    from app.core.database import get_session
    from app.main import create_app
    from app.models.dashboard_user import DashboardUser

    campaign = await _make_campaign(db_session)
    other = await _make_campaign(db_session, slug="other-event")
    lead = await lead_service.capture_lead(db_session, "other-event", _lead())

    app = create_app()

    async def _get_session():
        yield db_session

    app.dependency_overrides[get_session] = _get_session
    user = DashboardUser(
        email="staff@churchgate.com", role="admin", owner_id=None, hashed_password="x"
    )
    app.dependency_overrides[get_current_user] = lambda: user

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        res = await c.post(f"/api/v1/campaigns/{campaign.id}/leads/{lead.id}/resend-pack")
    assert res.status_code == 404
    assert other.id != campaign.id
