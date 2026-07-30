from app.models.trade_lead import TradeLead
from app.models.trade_program import STATUS_ACTIVE, TradeProgram
from app.services.trade_mailer import build_lead_notification_email


def _trade_lead(**overrides) -> TradeLead:
    data = {
        "trade_program_id": 1,
        "registration_id": "reg-1",
        "participant_index": 1,
        "is_primary": True,
        "first_name": "Amaka",
        "last_name": "Eze",
        "email": "amaka@example.com",
        "company": "Depafek Foods",
        "job_title": "MD",
        "responses": {},
    }
    data.update(overrides)
    return TradeLead(**data)


def _program(**overrides) -> TradeProgram:
    data = {
        "id": 1,
        "slug": "canton-fair-2026",
        "name": "Canton Fair",
        "status": STATUS_ACTIVE,
        "config": {},
    }
    data.update(overrides)
    return TradeProgram(**data)


def test_notification_uses_program_name_not_export_launchpad() -> None:
    lead = _trade_lead()
    subject, html, text = build_lead_notification_email([lead], _program())
    assert subject == "New Canton Fair Application: Amaka Eze — Depafek Foods"
    assert "Export Launchpad" not in subject
    assert "Export Launchpad" not in html
    assert "Export Launchpad" not in text
    assert "New Canton Fair Application" in html


def test_notification_renders_mission_specific_response_fields() -> None:
    lead = _trade_lead(
        responses={
            "sector_of_activities": "Manufacturing",
            "passport_number": "A1234567",
            "ticket_type": "Frequent Traveller (₦250,000)",
        }
    )
    subject, html, text = build_lead_notification_email([lead], _program())
    assert "Sector Of Activities" in html
    assert "Manufacturing" in html
    assert "Passport Number" in html
    assert "A1234567" in html
    assert "Sector Of Activities:" in text
    assert "Manufacturing" in text


def test_notification_excludes_second_participant_and_consent_keys_from_details() -> None:
    lead = _trade_lead(
        responses={
            "sector_of_activities": "Manufacturing",
            "second_participant": {"first_name": "X"},
            "consent_terms": True,
            "consent_data_processing": True,
        }
    )
    _subject, html, _text = build_lead_notification_email([lead], _program())
    assert "Sector Of Activities" in html
    assert "Consent Terms" not in html
    assert "Consent Data Processing" not in html


def test_notification_omits_details_section_when_responses_empty() -> None:
    lead = _trade_lead(responses={})
    _subject, html, _text = build_lead_notification_email([lead], _program())
    assert "TITLE" in html
    assert "MD" in html
