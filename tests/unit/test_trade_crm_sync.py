from app.models.trade_lead import TradeLead
from app.models.trade_program import STATUS_ACTIVE, TradeProgram
from app.services.trade_crm_sync import build_contact_payload


def _trade_lead(**overrides) -> TradeLead:
    data = {
        "trade_program_id": 1,
        "registration_id": "reg-1",
        "participant_index": 1,
        "is_primary": True,
        "first_name": "Amaka",
        "last_name": "Eze",
        "email": "amaka@example.com",
        "phone": "+2348012345678",
        "company": "Depafek Foods",
    }
    data.update(overrides)
    return TradeLead(**data)


def _program() -> TradeProgram:
    return TradeProgram(
        id=1, slug="export-launchpad-2026", name="Export Launchpad", status=STATUS_ACTIVE, config={}
    )


def test_payload_carries_export_fields() -> None:
    lead = _trade_lead(
        country="Nigeria", industry_sector="Tertiary sector", company_founded="2-5 years"
    )
    payload = build_contact_payload(lead, _program())
    cf = payload["contact"]["custom_field"]
    assert cf["cf_country"] == "Nigeria"
    assert cf["cf_industry_sector"] == "Tertiary sector"
    assert cf["cf_company_founded"] == "2-5 years"
    assert cf["cf_campaign"] == "export-launchpad-2026"


def test_payload_dedupes_by_participant_email() -> None:
    lead = _trade_lead(email="second@example.com")
    payload = build_contact_payload(lead, _program())
    assert payload["unique_identifier"]["emails"] == "second@example.com"
    assert payload["contact"]["emails"] == [{"value": "second@example.com", "is_primary": True}]


def test_payload_eligibility_status_placeholder() -> None:
    lead = _trade_lead(eligibility_status="submitted")
    payload = build_contact_payload(lead, _program())
    assert payload["contact"]["custom_field"]["cf_eligibility_status"] == "submitted"
