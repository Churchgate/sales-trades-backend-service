from datetime import UTC, datetime

from app.models.deal import DealSnapshot
from app.services.change_detection import detect_changes

OCCURRED_AT = datetime(2026, 6, 15, 13, 30, tzinfo=UTC)


def test_new_deal_emits_created_event() -> None:
    events = detect_changes(
        deal_id=1,
        existing=None,
        new_pipeline_id=100,
        new_stage_id=10,
        new_owner_id=5,
        occurred_at=OCCURRED_AT,
        source="webhook",
        raw_payload={},
    )

    assert [e.event_type for e in events] == ["created"]
    assert events[0].new_pipeline_id == 100
    assert events[0].new_stage_id == 10
    assert events[0].new_owner_id == 5


def test_no_change_emits_nothing() -> None:
    existing = DealSnapshot(deal_id=1, pipeline_id=100, stage_id=10, owner_id=5)

    events = detect_changes(
        deal_id=1,
        existing=existing,
        new_pipeline_id=100,
        new_stage_id=10,
        new_owner_id=5,
        occurred_at=OCCURRED_AT,
        source="webhook",
        raw_payload={},
    )

    assert events == []


def test_stage_change_emits_stage_change_event() -> None:
    existing = DealSnapshot(deal_id=1, pipeline_id=100, stage_id=10, owner_id=5)

    events = detect_changes(
        deal_id=1,
        existing=existing,
        new_pipeline_id=100,
        new_stage_id=11,
        new_owner_id=5,
        occurred_at=OCCURRED_AT,
        source="webhook",
        raw_payload={},
    )

    assert [e.event_type for e in events] == ["stage_change"]
    event = events[0]
    assert event.old_stage_id == 10
    assert event.new_stage_id == 11
    assert event.old_pipeline_id == 100
    assert event.new_pipeline_id == 100


def test_owner_change_emits_owner_change_event() -> None:
    existing = DealSnapshot(deal_id=1, pipeline_id=100, stage_id=10, owner_id=5)

    events = detect_changes(
        deal_id=1,
        existing=existing,
        new_pipeline_id=100,
        new_stage_id=10,
        new_owner_id=6,
        occurred_at=OCCURRED_AT,
        source="webhook",
        raw_payload={},
    )

    assert [e.event_type for e in events] == ["owner_change"]
    assert events[0].old_owner_id == 5
    assert events[0].new_owner_id == 6


def test_stage_and_owner_change_emit_both_events() -> None:
    existing = DealSnapshot(deal_id=1, pipeline_id=100, stage_id=10, owner_id=5)

    events = detect_changes(
        deal_id=1,
        existing=existing,
        new_pipeline_id=200,
        new_stage_id=20,
        new_owner_id=6,
        occurred_at=OCCURRED_AT,
        source="webhook",
        raw_payload={},
    )

    assert [e.event_type for e in events] == ["stage_change", "owner_change"]
