"""JWT access token signing, verification, and opaque token hashing."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import jwt
from pydantic import BaseModel, ConfigDict

from email_discovery_api.config import Settings, get_settings


class InvalidTokenError(Exception):
    """Raised when token validation fails for any reason."""


class AccessTokenPayload(BaseModel):
    """Validated JWT access token payload."""

    model_config = ConfigDict(frozen=True)

    user_id: UUID
    organization_id: UUID
    auth_version: int
    jti: UUID


def hash_token(raw_token: str) -> str:
    """Compute SHA-256 digest of raw token as 64 lowercase hex characters."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest().lower()


def generate_opaque_token() -> str:
    """Generate cryptographically secure 256-bit opaque token."""
    return secrets.token_urlsafe(32)


def verify_csrf_token(raw_csrf_header: str | None, stored_csrf_hash: str) -> bool:
    """Verify raw CSRF header token against stored SHA-256 hash using constant-time comparison."""
    if not raw_csrf_header or len(raw_csrf_header.strip()) == 0:
        return False
    computed_hash = hash_token(raw_csrf_header.strip())
    return hmac.compare_digest(computed_hash, stored_csrf_hash)


class TokenService:
    """JWT and token security service."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._secret = self._settings.jwt_secret_key.get_secret_value()
        self._algorithm = "HS256"  # Fixed to HS256
        self._issuer = self._settings.jwt_issuer
        self._audience = self._settings.jwt_audience
        self._access_ttl = timedelta(minutes=self._settings.access_token_ttl_minutes)
        self._refresh_ttl = timedelta(days=self._settings.refresh_token_ttl_days)
        self._leeway = timedelta(seconds=self._settings.clock_skew_seconds)

    def create_access_token(
        self,
        user_id: UUID,
        organization_id: UUID,
        auth_version: int,
        now: datetime | None = None,
    ) -> str:
        """Create signed HS256 JWT access token with all required claims."""
        current_time = now or datetime.now(UTC)
        claims = {
            "sub": str(user_id),
            "org": str(organization_id),
            "ver": int(auth_version),
            "jti": str(uuid.uuid4()),
            "typ": "access",
            "iss": self._issuer,
            "aud": self._audience,
            "iat": int(current_time.timestamp()),
            "nbf": int(current_time.timestamp()),
            "exp": int((current_time + self._access_ttl).timestamp()),
        }
        return jwt.encode(claims, self._secret, algorithm=self._algorithm)  # pyright: ignore[reportUnknownMemberType]

    def decode_access_token(self, token: str, now: datetime | None = None) -> AccessTokenPayload:
        """Strictly decode and validate access token claims."""
        try:
            payload: dict[str, Any] = jwt.decode(  # pyright: ignore[reportUnknownMemberType]
                token,
                self._secret,
                algorithms=[self._algorithm],
                issuer=self._issuer,
                audience=self._audience,
                leeway=self._leeway,
                options={
                    "verify_signature": True,
                    "verify_exp": True,
                    "verify_nbf": True,
                    "verify_iat": True,
                    "verify_aud": True,
                    "verify_iss": True,
                    "require": [
                        "sub",
                        "org",
                        "ver",
                        "jti",
                        "typ",
                        "iss",
                        "aud",
                        "iat",
                        "nbf",
                        "exp",
                    ],
                },
            )
        except Exception as exc:
            raise InvalidTokenError("Invalid access token signature or claims") from exc

        # Strict claim type and format validation
        if payload.get("typ") != "access":
            raise InvalidTokenError("Invalid token type")

        ver = payload.get("ver")
        if isinstance(ver, bool) or not isinstance(ver, int) or ver < 1:
            raise InvalidTokenError("Invalid auth_version claim")

        try:
            user_id = UUID(str(payload["sub"]))
            organization_id = UUID(str(payload["org"]))
            jti = UUID(str(payload["jti"]))
        except (ValueError, TypeError) as exc:
            raise InvalidTokenError("Invalid UUID claim syntax") from exc

        return AccessTokenPayload(
            user_id=user_id,
            organization_id=organization_id,
            auth_version=ver,
            jti=jti,
        )

    def generate_refresh_credentials(
        self, now: datetime | None = None
    ) -> tuple[str, str, str, str, datetime]:
        """Generate raw refresh token, raw CSRF token, their digests, and expiration time."""
        current_time = now or datetime.now(UTC)
        raw_refresh_token = generate_opaque_token()
        raw_csrf_token = generate_opaque_token()
        refresh_token_hash = hash_token(raw_refresh_token)
        csrf_token_hash = hash_token(raw_csrf_token)
        expires_at = current_time + self._refresh_ttl
        return (
            raw_refresh_token,
            raw_csrf_token,
            refresh_token_hash,
            csrf_token_hash,
            expires_at,
        )
