from app.models.trade_lead import TradeLead
from app.models.trade_program import STATUS_ACTIVE, TradeProgram
from app.services.trade_crm_sync import (
    _EXPORT_LAUNCHPAD_COHORT1_LEAD_SOURCE_ID,
    _LEAD_STAGE_ID,
    _NEW_STATUS_ID,
    build_contact_payload,
)


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


def test_payload_uses_dedicated_export_launchpad_lead_source() -> None:
    payload = build_contact_payload(_trade_lead(), _program())
    assert payload["contact"]["lead_source_id"] == _EXPORT_LAUNCHPAD_COHORT1_LEAD_SOURCE_ID


def test_payload_falls_back_to_export_launchpad_source_when_program_unset() -> None:
    """A program with crm_lead_source_id explicitly None (e.g. a newly-seeded
    mission pending manual Freshsales setup) must also fall back — not just an
    absent key — matching program.config's `.get(...) or default` fallback."""
    program = _program()
    program.config = {"crm_lead_source_id": None}
    payload = build_contact_payload(_trade_lead(), program)
    assert payload["contact"]["lead_source_id"] == _EXPORT_LAUNCHPAD_COHORT1_LEAD_SOURCE_ID


def test_payload_uses_program_specific_lead_source_when_set() -> None:
    program = _program()
    program.config = {"crm_lead_source_id": 17009999999}
    payload = build_contact_payload(_trade_lead(), program)
    assert payload["contact"]["lead_source_id"] == 17009999999


def test_payload_starts_at_lead_stage_new_status() -> None:
    payload = build_contact_payload(_trade_lead(), _program())
    assert payload["contact"]["lifecycle_stage_id"] == _LEAD_STAGE_ID
    assert payload["contact"]["contact_status_id"] == _NEW_STATUS_ID
