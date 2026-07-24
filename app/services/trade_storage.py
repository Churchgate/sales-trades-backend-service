"""Supabase Storage (S3-compatible API) for Trade eligibility documents.

Same Supabase project as the database and the public `campaign-assets`
bucket, but `trade_documents_bucket` is private — never exposed at
/storage/v1/object/public/..., only via short-lived presigned GET URLs (see
get_download_url).

boto3 is sync-only, so every call runs on a worker thread via
anyio.to_thread.run_sync — same pattern as web_analytics.py's blocking GA
client call.
"""

import uuid

import anyio.to_thread
import boto3
from botocore.client import Config as BotoConfig

from app.core.config import Settings, get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_PRESIGNED_URL_TTL_SECONDS = 300


class StorageNotConfiguredError(Exception):
    """Supabase S3 credentials aren't set — see Settings.supabase_s3_configured."""


def _client(settings: Settings):
    return boto3.client(
        "s3",
        endpoint_url=settings.supabase_s3_endpoint_url,
        aws_access_key_id=settings.supabase_s3_access_key_id,
        aws_secret_access_key=settings.supabase_s3_secret_access_key,
        config=BotoConfig(signature_version="s3v4"),
        region_name=settings.supabase_s3_region,
    )


def build_storage_key(
    program_slug: str, registration_id: str, document_key: str, file_name: str
) -> str:
    ext = ("." + file_name.rsplit(".", 1)[-1]) if "." in file_name else ""
    return f"{program_slug}/{registration_id}/{document_key}-{uuid.uuid4().hex[:8]}{ext}"


def _put_object(
    settings: Settings, storage_key: str, body: bytes, content_type: str | None
) -> None:
    _client(settings).put_object(
        Bucket=settings.trade_documents_bucket,
        Key=storage_key,
        Body=body,
        ContentType=content_type or "application/octet-stream",
    )


def _delete_object(settings: Settings, storage_key: str) -> None:
    _client(settings).delete_object(Bucket=settings.trade_documents_bucket, Key=storage_key)


def _presigned_url(settings: Settings, storage_key: str, file_name: str) -> str:
    return _client(settings).generate_presigned_url(
        "get_object",
        Params={
            "Bucket": settings.trade_documents_bucket,
            "Key": storage_key,
            "ResponseContentDisposition": f'attachment; filename="{file_name}"',
        },
        ExpiresIn=_PRESIGNED_URL_TTL_SECONDS,
    )


async def upload_document(
    *, storage_key: str, body: bytes, content_type: str | None, settings: Settings | None = None
) -> None:
    settings = settings or get_settings()
    if not settings.supabase_s3_configured:
        raise StorageNotConfiguredError("Supabase S3 credentials are not configured")
    await anyio.to_thread.run_sync(_put_object, settings, storage_key, body, content_type)


async def delete_document(storage_key: str, settings: Settings | None = None) -> None:
    """Best-effort cleanup of a superseded upload. Never raises — an orphaned
    object is a minor cost, not worth failing the new upload over."""
    settings = settings or get_settings()
    if not settings.supabase_s3_configured:
        return
    try:
        await anyio.to_thread.run_sync(_delete_object, settings, storage_key)
    except Exception:
        logger.exception("trade document delete failed", storage_key=storage_key)


async def get_download_url(
    storage_key: str, file_name: str, settings: Settings | None = None
) -> str | None:
    settings = settings or get_settings()
    if not settings.supabase_s3_configured:
        return None
    return await anyio.to_thread.run_sync(_presigned_url, settings, storage_key, file_name)
