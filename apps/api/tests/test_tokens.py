"""Unit tests for JWT signing, verification, fixed HS256 algorithm, and opaque tokens."""

import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from email_discovery_api.config import Settings
from email_discovery_api.services.tokens import (
    InvalidTokenError,
    TokenService,
    hash_token,
    verify_csrf_token,
)


@pytest.fixture
def token_service(test_settings: Settings) -> TokenService:
    return TokenService(test_settings)


def test_jwt_create_and_decode_success(token_service: TokenService) -> None:
    """Verify standard JWT access token signing and claim decoding."""
    user_id = uuid.uuid4()
    org_id = uuid.uuid4()
    auth_ver = 1

    token_str = token_service.create_access_token(user_id, org_id, auth_ver)
    payload = token_service.decode_access_token(token_str)

    assert payload.user_id == user_id
    assert payload.organization_id == org_id
    assert payload.auth_version == auth_ver
    assert payload.jti is not None


def test_jwt_fixed_algorithm_enforcement(
    token_service: TokenService, test_settings: Settings
) -> None:
    """Verify tokens signed with non-HS256 or 'none' algorithms are rejected."""
    user_id = uuid.uuid4()
    org_id = uuid.uuid4()
    secret = test_settings.jwt_secret_key.get_secret_value()

    # None algorithm attack token
    untrusted_none = jwt.encode(  # pyright: ignore[reportUnknownMemberType]
        {"sub": str(user_id), "org": str(org_id), "ver": 1, "typ": "access"}, "", algorithm="none"
    )
    with pytest.raises(InvalidTokenError):
        token_service.decode_access_token(untrusted_none)

    # Wrong claims: typ != access
    wrong_type = jwt.encode(  # pyright: ignore[reportUnknownMemberType]
        {
            "sub": str(user_id),
            "org": str(org_id),
            "ver": 1,
            "jti": str(uuid.uuid4()),
            "typ": "refresh",
            "iss": test_settings.jwt_issuer,
            "aud": test_settings.jwt_audience,
            "iat": int(datetime.now(UTC).timestamp()),
            "nbf": int(datetime.now(UTC).timestamp()),
            "exp": int((datetime.now(UTC) + timedelta(minutes=15)).timestamp()),
        },
        secret,
        algorithm="HS256",
    )
    with pytest.raises(InvalidTokenError):
        token_service.decode_access_token(wrong_type)


def test_jwt_boolean_version_rejected(token_service: TokenService, test_settings: Settings) -> None:
    """Verify boolean ver claim is strictly rejected."""
    user_id = uuid.uuid4()
    org_id = uuid.uuid4()
    secret = test_settings.jwt_secret_key.get_secret_value()

    bool_ver_token = jwt.encode(  # pyright: ignore[reportUnknownMemberType]
        {
            "sub": str(user_id),
            "org": str(org_id),
            "ver": True,
            "jti": str(uuid.uuid4()),
            "typ": "access",
            "iss": test_settings.jwt_issuer,
            "aud": test_settings.jwt_audience,
            "iat": int(datetime.now(UTC).timestamp()),
            "nbf": int(datetime.now(UTC).timestamp()),
            "exp": int((datetime.now(UTC) + timedelta(minutes=15)).timestamp()),
        },
        secret,
        algorithm="HS256",
    )
    with pytest.raises(InvalidTokenError):
        token_service.decode_access_token(bool_ver_token)


def test_token_hash_and_csrf_verification() -> None:
    """Verify SHA-256 token hashing and constant-time CSRF verification."""
    raw_csrf = "my-secret-csrf-token-123"
    csrf_digest = hash_token(raw_csrf)

    assert len(csrf_digest) == 64
    assert verify_csrf_token(raw_csrf, csrf_digest) is True
    assert verify_csrf_token("wrong-csrf-token", csrf_digest) is False
    assert verify_csrf_token(None, csrf_digest) is False
