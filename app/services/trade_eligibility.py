"""Eligibility-document submission for a Trade registration.

Documents are uploaded directly by wtcabuja.com (see
POST /trade/programs/{slug}/eligibility) — one call per file, matched to a
registration by `registration_id`. `document_key` must be one of the
program's `config["required_documents"]` keys; re-uploading the same key
replaces the previous file. `eligibility_status` on the registration's
participant row(s) is a simple rollup against that config's required (vs
optional) documents — there's no separate "request" step, so PENDING here
means "some but not all required documents are in," not the original
not-yet-requested sense of the placeholder column.
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.trade_document import TradeDocument
from app.models.trade_lead import (
    ELIGIBILITY_NOT_REQUESTED,
    ELIGIBILITY_PENDING,
    ELIGIBILITY_SUBMITTED,
)
from app.models.trade_program import TradeProgram
from app.repositories import trade_repo
from app.services import trade_storage


class UnknownDocumentKeyError(Exception):
    pass


class RegistrationNotFoundError(Exception):
    pass


def _required_documents(program: TradeProgram) -> list[dict[str, Any]]:
    return (program.config or {}).get("required_documents", [])


def _document_keys(program: TradeProgram) -> set[str]:
    return {d["key"] for d in _required_documents(program)}


def _required_keys(program: TradeProgram) -> set[str]:
    return {d["key"] for d in _required_documents(program) if d.get("required")}


def compute_eligibility_status(present_keys: set[str], program: TradeProgram) -> str:
    required = _required_keys(program)
    if not present_keys:
        return ELIGIBILITY_NOT_REQUESTED
    if required and required.issubset(present_keys):
        return ELIGIBILITY_SUBMITTED
    if not required:
        return ELIGIBILITY_SUBMITTED
    return ELIGIBILITY_PENDING


async def submit_document(
    session: AsyncSession,
    program: TradeProgram,
    *,
    registration_id: str,
    document_key: str,
    file_name: str,
    content_type: str | None,
    body: bytes,
) -> tuple[TradeDocument, str]:
    """Store one document and recompute eligibility_status for the
    registration. Returns (document row, new eligibility_status)."""
    if document_key not in _document_keys(program):
        raise UnknownDocumentKeyError(
            f"'{document_key}' is not a required document for {program.slug}"
        )

    participants = await trade_repo.list_by_registration(session, registration_id)
    if not participants:
        raise RegistrationNotFoundError(f"no registration '{registration_id}'")

    storage_key = trade_storage.build_storage_key(
        program.slug, registration_id, document_key, file_name
    )
    await trade_storage.upload_document(
        storage_key=storage_key, body=body, content_type=content_type
    )

    existing = await trade_repo.get_document(session, registration_id, document_key)
    if existing is not None:
        await trade_storage.delete_document(existing.storage_key)
        existing.storage_key = storage_key
        existing.file_name = file_name
        existing.content_type = content_type
        existing.size_bytes = len(body)
        existing.uploaded_at = datetime.now(UTC)
        document = await trade_repo.upsert_document(session, existing)
    else:
        document = await trade_repo.upsert_document(
            session,
            TradeDocument(
                trade_program_id=program.id,
                registration_id=registration_id,
                document_key=document_key,
                storage_key=storage_key,
                file_name=file_name,
                content_type=content_type,
                size_bytes=len(body),
            ),
        )

    documents = await trade_repo.list_documents(session, registration_id)
    present_keys = {d.document_key for d in documents}
    new_status = compute_eligibility_status(present_keys, program)

    for lead in participants:
        lead.eligibility_status = new_status
        if new_status == ELIGIBILITY_SUBMITTED and lead.eligibility_submitted_at is None:
            lead.eligibility_submitted_at = datetime.now(UTC)
        await trade_repo.update_lead(session, lead)

    return document, new_status
