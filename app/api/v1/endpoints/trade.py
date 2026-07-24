"""Trade area API — Trade Programs (starting with Export Launchpad Boot Camp
2026) and, later, Trade Membership.

Two public, unauthenticated endpoints exist for wtcabuja.com to call
directly: registration capture (POST /programs/{slug}/register) and
eligibility-document upload (POST /programs/{slug}/eligibility). Everything
else is staff-only, view-all for every admin role (rep scoping is a
placeholder column, not wired in yet — see TradeLead.owner_id).
"""

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status

from app.api.dependencies import SessionDep, require_role
from app.models.trade_lead import TradeLead
from app.repositories import trade_repo
from app.schemas.trade import (
    TradeDocumentOut,
    TradeDocumentsListResponse,
    TradeEligibilitySubmitResponse,
    TradeLeadDetailResponse,
    TradeLeadOut,
    TradeLeadsListResponse,
    TradeParticipantRef,
    TradeProgramDetailResponse,
    TradeProgramOut,
    TradeProgramsListResponse,
    TradeProgramStats,
    TradeRegistrationCaptureResponse,
    TradeRegistrationCreateRequest,
    TradeRegistrationDetailResponse,
    TradeRegistrationOut,
)
from app.services import (
    trade_capture,
    trade_crm_sync,
    trade_eligibility,
    trade_mailer,
    trade_storage,
)

router = APIRouter(prefix="/trade", tags=["trade"])

# Generous but bounded — this endpoint is public/unauthenticated, so an
# unbounded body would let anyone push arbitrary storage costs onto us.
_MAX_DOCUMENT_SIZE_BYTES = 15 * 1024 * 1024

_ADMIN_ROLES = ("admin", "superadmin", "hod", "team_lead", "rep")


def _program_out(program) -> TradeProgramOut:
    return TradeProgramOut.model_validate(program, from_attributes=True)


async def _co_participant_ref(session: SessionDep, lead: TradeLead) -> TradeParticipantRef | None:
    siblings = await trade_repo.list_by_registration(session, lead.registration_id)
    other = next((s for s in siblings if s.id != lead.id), None)
    if other is None:
        return None
    return TradeParticipantRef(
        id=other.id,
        first_name=other.first_name,
        last_name=other.last_name,
        email=other.email or None,
        is_primary=other.is_primary,
    )


async def _lead_out(session: SessionDep, lead: TradeLead) -> TradeLeadOut:
    out = TradeLeadOut.model_validate(lead, from_attributes=True)
    out.co_participant = await _co_participant_ref(session, lead)
    return out


async def _require_program(session: SessionDep, program_id: int):
    program = await trade_repo.get_program(session, program_id)
    if program is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Program not found")
    return program


# --- Public: wtcabuja.com registration capture ---


@router.post("/programs/{slug}/register", status_code=status.HTTP_201_CREATED)
async def register(
    slug: str, body: TradeRegistrationCreateRequest, session: SessionDep
) -> TradeRegistrationCaptureResponse:
    """Public — a company registers (1 or 2 participants) for a Trade
    program. Saved immediately with crm_sync_status=pending; CRM push
    happens later via the scheduled trade_crm_sync job, same offline-safe
    contract as the old campaign lead-capture endpoint this replaces."""
    try:
        program, participants, created = await trade_capture.capture_registration(
            session, slug.strip(), body
        )
    except trade_capture.ProgramNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except trade_capture.ProgramInactiveError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    # Only on a genuinely new registration — an idempotent resubmit (same
    # email retried) must not re-send the confirmation. Gated the same way
    # campaigns.py gates the campaign-era version of this email.
    if created and (program.config or {}).get("application_confirmation"):
        await trade_mailer.send_application_confirmation(participants, program)

    return TradeRegistrationCaptureResponse(
        status_code=status.HTTP_201_CREATED,
        created=created,
        registration=TradeRegistrationOut(
            registration_id=participants[0].registration_id,
            participants=[await _lead_out(session, p) for p in participants],
        ),
    )


@router.post("/programs/{slug}/eligibility", status_code=status.HTTP_201_CREATED)
async def submit_eligibility_document(
    slug: str,
    session: SessionDep,
    registration_id: Annotated[str, Form()],
    document_key: Annotated[str, Form()],
    file: Annotated[UploadFile, File()],
) -> TradeEligibilitySubmitResponse:
    """Public — wtcabuja.com uploads one eligibility document at a time for a
    registration it already has (from the /register response). Re-uploading
    the same document_key replaces the previous file. Requires Supabase
    Storage to be configured; returns 503 (not 500) if it isn't yet."""
    program = await trade_repo.get_program_by_slug(session, slug.strip())
    if program is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Program not found")

    body = await file.read()
    if len(body) > _MAX_DOCUMENT_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="File too large"
        )

    try:
        document, new_status = await trade_eligibility.submit_document(
            session,
            program,
            registration_id=registration_id.strip(),
            document_key=document_key.strip(),
            file_name=file.filename or document_key,
            content_type=file.content_type,
            body=body,
        )
    except trade_eligibility.UnknownDocumentKeyError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except trade_eligibility.RegistrationNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except trade_storage.StorageNotConfiguredError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc

    return TradeEligibilitySubmitResponse(
        status_code=status.HTTP_201_CREATED,
        document=TradeDocumentOut.model_validate(document, from_attributes=True),
        eligibility_status=new_status,
    )


@router.get("/programs", dependencies=[Depends(require_role(*_ADMIN_ROLES))])
async def list_programs(session: SessionDep) -> TradeProgramsListResponse:
    programs = await trade_repo.list_programs(session)
    return TradeProgramsListResponse(
        status_code=status.HTTP_200_OK, programs=[_program_out(p) for p in programs]
    )


@router.get("/programs/{program_id}", dependencies=[Depends(require_role(*_ADMIN_ROLES))])
async def get_program(program_id: int, session: SessionDep) -> TradeProgramDetailResponse:
    program = await _require_program(session, program_id)
    stats = await trade_repo.program_stats(session, program_id)
    return TradeProgramDetailResponse(
        status_code=status.HTTP_200_OK,
        program=_program_out(program),
        stats=TradeProgramStats(**stats),
    )


@router.get(
    "/programs/{program_id}/participants", dependencies=[Depends(require_role(*_ADMIN_ROLES))]
)
async def list_participants(
    program_id: int,
    session: SessionDep,
    crm_sync_status: Annotated[str | None, Query()] = None,
    eligibility_status: Annotated[str | None, Query()] = None,
    search: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> TradeLeadsListResponse:
    await _require_program(session, program_id)
    leads = await trade_repo.list_leads(
        session,
        program_id,
        crm_sync_status=crm_sync_status,
        eligibility_status=eligibility_status,
        search=search,
        limit=limit,
        offset=offset,
    )
    total = await trade_repo.count_leads(
        session,
        program_id,
        crm_sync_status=crm_sync_status,
        eligibility_status=eligibility_status,
        search=search,
    )
    return TradeLeadsListResponse(
        status_code=status.HTTP_200_OK,
        leads=[await _lead_out(session, lead) for lead in leads],
        total=total,
    )


@router.get(
    "/registrations/{registration_id}", dependencies=[Depends(require_role(*_ADMIN_ROLES))]
)
async def get_registration(
    registration_id: str, session: SessionDep
) -> TradeRegistrationDetailResponse:
    """Both participant rows of one registration, for the detail dialog."""
    leads = await trade_repo.list_by_registration(session, registration_id)
    if not leads:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Registration not found"
        )
    return TradeRegistrationDetailResponse(
        status_code=status.HTTP_200_OK,
        registration=TradeRegistrationOut(
            registration_id=registration_id,
            participants=[await _lead_out(session, lead) for lead in leads],
        ),
    )


@router.get(
    "/registrations/{registration_id}/documents",
    dependencies=[Depends(require_role(*_ADMIN_ROLES))],
)
async def list_registration_documents(
    registration_id: str, session: SessionDep
) -> TradeDocumentsListResponse:
    """Submitted eligibility documents for a registration, each with a
    5-minute presigned download URL (None if Supabase Storage isn't
    configured yet, e.g. in dev)."""
    documents = await trade_repo.list_documents(session, registration_id)
    out = []
    for doc in documents:
        download_url = await trade_storage.get_download_url(doc.storage_key, doc.file_name)
        item = TradeDocumentOut.model_validate(doc, from_attributes=True)
        item.download_url = download_url
        out.append(item)
    return TradeDocumentsListResponse(status_code=status.HTTP_200_OK, documents=out)


@router.get("/participants/{lead_id}", dependencies=[Depends(require_role(*_ADMIN_ROLES))])
async def get_participant(lead_id: int, session: SessionDep) -> TradeLeadDetailResponse:
    lead = await trade_repo.get_lead(session, lead_id)
    if lead is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Participant not found")
    return TradeLeadDetailResponse(
        status_code=status.HTTP_200_OK, lead=await _lead_out(session, lead)
    )


@router.post(
    "/programs/{program_id}/resync",
    dependencies=[Depends(require_role("admin", "superadmin"))],
)
async def resync_participants(
    program_id: int, session: SessionDep
) -> TradeProgramDetailResponse:
    """Re-attempt CRM sync for pending/failed trade participants, then return
    fresh stats. Never touches already-`synced` rows (see trade_crm_sync)."""
    program = await _require_program(session, program_id)
    await trade_crm_sync.sync_pending_trade(session)
    stats = await trade_repo.program_stats(session, program_id)
    return TradeProgramDetailResponse(
        status_code=status.HTTP_200_OK,
        program=_program_out(program),
        stats=TradeProgramStats(**stats),
    )
