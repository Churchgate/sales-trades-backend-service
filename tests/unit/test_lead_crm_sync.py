from app.models.campaign import STATUS_ACTIVE, Campaign
from app.models.lead import Lead
from app.services.lead_crm_sync import build_contact_payload


def _lead(**overrides) -> Lead:
    data = {
        "campaign_id": 1, "first_name": "Ada", "last_name": "Lovelace",
        "email": "ada@example.com", "phone": "+2348012345678", "company": "Energy Co",
    }
    data.update(overrides)
    return Lead(**data)


def _campaign(slug: str) -> Campaign:
    return Campaign(id=1, slug=slug, name=slug, status=STATUS_ACTIVE, config={})


def test_payload_carries_icp_score() -> None:
    lead = _lead(icp_score=86)
    payload = build_contact_payload(lead, _campaign("nog-2026"))
    assert payload["contact"]["custom_field"]["cf_icp_score"] == 86


def test_payload_icp_score_null_before_scoring() -> None:
    lead = _lead()
    payload = build_contact_payload(lead, _campaign("nog-2026"))
    assert payload["contact"]["custom_field"]["cf_icp_score"] is None


def test_website_lead_gets_status_new() -> None:
    lead = _lead()
    payload = build_contact_payload(lead, _campaign("wtcabuja-website"))
    assert payload["contact"]["contact_status_id"] == 17000261833


def test_nog_lead_does_not_get_website_status() -> None:
    lead = _lead()
    payload = build_contact_payload(lead, _campaign("nog-2026"))
    assert "contact_status_id" not in payload["contact"]
