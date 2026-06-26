"""JWT issuance/verification and bcrypt password hashing."""

import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
import jwt

from app.core.config import get_settings

settings = get_settings()


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def generate_temp_password() -> str:
    """A URL-safe random temporary password for invited users (~16 chars)."""
    return secrets.token_urlsafe(12)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except Exception:
        return False


def _encode(subject: str, role: str, token_type: str, expires_delta: timedelta) -> str:
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": subject,
        "role": role,
        "type": token_type,
        "iat": now,
        "exp": now + expires_delta,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_access_token(subject: str, role: str) -> str:
    return _encode(
        subject, role, "access", timedelta(minutes=settings.jwt_access_token_expire_minutes)
    )


def create_refresh_token(subject: str, role: str) -> str:
    return _encode(
        subject, role, "refresh", timedelta(minutes=settings.jwt_refresh_token_expire_minutes)
    )


def decode_token(token: str) -> dict[str, Any]:
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
