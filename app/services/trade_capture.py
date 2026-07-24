"""Public registration capture for Trade programs (wtcabuja.com forms).

Capture's one job is to never lose a registration: validate minimally,
persist immediately, dedup by (program, email), and never block on the
network — rows save crm_sync_status='pending' and the scheduled
trade_crm_sync_job pushes them to Freshsales later. Mirrors the shape and
guarantees of services/lead_service.py's campaign capture.
"""

import uuid
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.trade_lead import CRM_PENDING, TradeLead
from app.models.trade_program import STATUS_ACTIVE, TradeProgram
from app.repositories import trade_repo
from app.schemas.trade import TradeRegistrationCreateRequest

logger = get_logger(__name__)


class TradeCaptureError(Exception):
    """Base for trade-registration capture domain errors."""


class ProgramNotFoundError(TradeCaptureError):
    pass


class ProgramInactiveError(TradeCaptureError):
    pass


# Registration-level fields carried in `responses`, duplicated onto both
# participant rows of a registration. Names match the real production
# payload (verified against live wtcabuja.com/export-launchpad submissions),
# not just the public form's field labels.
SHARED_RESPONSE_KEYS = (
    "company_founded", "industry_sector", "sector_specification", "sector_other",
    "ownership", "operating_currency", "fiscal_year_start", "employee_count",
    "sources_internationally", "source_countries", "sells_internationally",
    "sales_countries", "topics_of_interest", "consent_terms",
    "consent_data_processing", "consent_liability_waiver", "consent_photo_video",
    "cohort_date", "wtc_location", "registered_address", "city", "postal_code",
    "country",
)


def shared_fields_from_responses(responses: dict[str, Any]) -> dict[str, Any]:
    return {k: responses.get(k) for k in SHARED_RESPONSE_KEYS}


def second_participant_from_responses(
    responses: dict[str, Any], primary_email: str
) -> tuple[dict[str, Any] | None, str | None]:
    """Returns (identity_kwargs, warning). None if there's no usable 2nd
    participant (it's optional — a nameless one is simply skipped). `warning`
    is set when the 2nd participant's email collides with the primary's (seen
    in production test data) — dropped rather than blocking the submission,
    since a duplicate email would violate the per-program unique index and
    isn't a distinct Freshsales contact anyway."""
    second = responses.get("second_participant") or {}
    first_name = (second.get("first_name") or "").strip()
    last_name = (second.get("last_name") or "").strip()
    if not first_name and not last_name:
        return None, None
    email = (second.get("email") or "").strip().lower()
    warning = None
    if email and email == primary_email:
        warning = f"2nd participant email '{email}' matches the primary's — dropped"
        email = ""
    return {
        "first_name": first_name or "—",
        "last_name": last_name or "—",
        "email": email,
        "phone": second.get("phone") or None,
        "job_title": second.get("job_title"),
        "responses": second,
    }, warning


def _apply_payload(
    lead: TradeLead, program_id: int, payload: TradeRegistrationCreateRequest
) -> None:
    lead.trade_program_id = program_id
    lead.first_name = payload.first_name
    lead.last_name = payload.last_name
    lead.phone = payload.phone
    lead.job_title = payload.job_title
    lead.company = payload.company
    lead.source = payload.source
    lead.captured_at = payload.captured_at
    lead.responses = payload.responses
    for key, value in shared_fields_from_responses(payload.responses).items():
        setattr(lead, key, value)


async def capture_registration(
    session: AsyncSession, slug: str, payload: TradeRegistrationCreateRequest
) -> tuple[TradeProgram, list[TradeLead], bool]:
    """Create (or dedup-merge) a registration for the active program `slug`.

    Returns (program, participants, created). Dedup is by (program_id,
    email) on the PRIMARY only — same idempotency mechanism as campaign lead
    capture: a retried submission lands on the same rows instead of
    duplicating. A unique-violation race on INSERT is treated the same as
    finding the row up front (re-fetch and merge)."""
    program = await trade_repo.get_program_by_slug(session, slug)
    if program is None:
        raise ProgramNotFoundError(f"no trade program with slug {slug}")
    if program.status != STATUS_ACTIVE:
        raise ProgramInactiveError(f"program {slug} is not accepting registrations")

    email = str(payload.email).strip().lower()
    existing = await trade_repo.get_by_program_email(session, program.id, email)
    created = existing is None
    primary = existing or TradeLead(
        trade_program_id=program.id,
        registration_id=str(uuid.uuid4()),
        participant_index=1,
        is_primary=True,
        email=email,
        crm_sync_status=CRM_PENDING,
    )
    _apply_payload(primary, program.id, payload)

    try:
        primary = await trade_repo.create_lead(session, primary) if created else (
            await trade_repo.update_lead(session, primary)
        )
    except IntegrityError:
        await session.rollback()
        existing = await trade_repo.get_by_program_email(session, program.id, email)
        if existing is None:
            raise
        created = False
        _apply_payload(existing, program.id, payload)
        primary = await trade_repo.update_lead(session, existing)

    participants = [primary]
    second_kwargs, warning = second_participant_from_responses(payload.responses, email)
    if warning:
        logger.warning("trade registration: dropping 2nd participant email", detail=warning)
    if second_kwargs:
        siblings = await trade_repo.list_by_registration(session, primary.registration_id)
        second = next((s for s in siblings if not s.is_primary), None)
        # company is a shared registration field (like the rest of `shared`) but
        # lives at the payload's top level, not inside `responses` — carry it
        # onto the 2nd participant the same way the transfer script does.
        shared = {**shared_fields_from_responses(payload.responses), "company": payload.company}
        if second is None:
            second = TradeLead(
                trade_program_id=program.id,
                registration_id=primary.registration_id,
                participant_index=2,
                is_primary=False,
                source=payload.source,
                crm_sync_status=CRM_PENDING,
                **second_kwargs,
                **shared,
            )
            second = await trade_repo.create_lead(session, second)
        else:
            for key, value in second_kwargs.items():
                setattr(second, key, value)
            for key, value in shared.items():
                setattr(second, key, value)
            second = await trade_repo.update_lead(session, second)
        participants.append(second)

    return program, participants, created
