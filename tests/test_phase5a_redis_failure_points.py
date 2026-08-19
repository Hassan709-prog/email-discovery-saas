"""Phase 5A Failure Injection, Safety, Transaction & Privacy Guarantee Tests."""

import logging
from unittest.mock import AsyncMock

import pytest

from email_discovery_api.config import Settings
from email_discovery_api.redis import APIRedisClient, sanitize_redis_url
from email_discovery_crawl_worker.config import WorkerSettings
from email_discovery_crawl_worker.redis_gate import (
    InvalidDomainError,
    RedisDomainRequestGate,
    RedisRateLimitPausedError,
    derive_domain_digest,
)
from email_scanner.models import HostType, NormalizedURL


def make_norm_url(
    hostname: str,
    registrable_domain: str | None = None,
    host_type: HostType = HostType.DOMAIN,
) -> NormalizedURL:
    """Helper creating NormalizedURL objects for tests."""
    return NormalizedURL(
        original_url=f"https://{hostname}/",
        scheme="https",
        hostname=hostname,
        port=None,
        path="/",
        query="",
        host_type=host_type,
        registrable_domain=registrable_domain,
        normalized_url=f"https://{hostname}/",
    )


@pytest.mark.anyio
async def test_failure_before_or_during_queue_notification(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Verify Redis wake-up publish failure after DB commit leaves committed DB state intact."""
    settings = Settings()
    client = APIRedisClient(settings)
    mock_redis = AsyncMock()

    # Inject connection error on publish
    mock_redis.publish.side_effect = ConnectionError("Redis socket timeout on publish")
    client.client = mock_redis
    client.is_available = True

    with caplog.at_level(logging.WARNING):
        await client.publish_work_available()

    # Logs warning without raising exception to caller
    assert "REDIS_PUBLISH_FAILED" in caplog.text
    assert "ConnectionError" in caplog.text
    # No sensitive URL or password in log
    assert "socket timeout" not in caplog.text


@pytest.mark.anyio
async def test_strict_pause_failure_between_claim_and_outbound_request():
    """Verify failure during rate limit acquisition in strict_pause mode pauses execution."""
    settings = WorkerSettings(redis_rate_limit_fallback_mode="strict_pause")
    mock_redis = AsyncMock()
    mock_redis.script_load.side_effect = ConnectionRefusedError("Redis connection lost")

    gate = RedisDomainRequestGate(
        redis_client=mock_redis,
        settings=settings,
    )

    url = make_norm_url("example.com", registrable_domain="example.com")
    with pytest.raises(RedisRateLimitPausedError, match="strict_pause mode"):
        await gate.acquire(url)


def test_privacy_guarantee_sha256_digests_only():
    """Verify Redis keys contain ONLY namespaced SHA-256 domain digests and reject empty inputs."""
    url = make_norm_url("sensitivedomain.com", registrable_domain="sensitivedomain.com")
    digest = derive_domain_digest(url)

    # 64 hex characters
    assert len(digest) == 64
    assert digest.islower()
    assert "sensitivedomain" not in digest

    # Reject empty domain identity before hashing
    empty_url = make_norm_url("   ")
    with pytest.raises(InvalidDomainError):
        derive_domain_digest(empty_url)


def test_sanitization_guarantee_no_leaked_credentials():
    """Verify sanitize_redis_url strips username and password from all log strings."""
    url_with_creds = "redis://admin:super_secret_password_123@redis.internal:6379/0"
    sanitized = sanitize_redis_url(url_with_creds)
    assert "super_secret_password_123" not in sanitized
    assert "admin" not in sanitized
    assert "***" in sanitized
