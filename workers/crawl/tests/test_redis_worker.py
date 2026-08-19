"""Unit tests for worker RedisDomainRequestGate and WorkerSettings."""

import logging
from unittest.mock import AsyncMock

import pytest

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


def test_cross_field_configuration_rejection():
    """Verify WorkerSettings rejects configurations invalid under max_domain_interval_ms formula."""
    # Under old min-interval formula: 60000 + 1000 + 5000 = 66000.
    # Must fail under max-interval formula (60000 + 60000 + 5000 = 125000).
    with pytest.raises(ValueError, match="Invalid Redis rate limit configuration"):
        WorkerSettings(
            min_domain_interval_ms=1000,
            max_domain_interval_ms=60000,
            max_reservation_horizon_ms=60000,
            safety_margin_ms=5000,
            max_ttl_ms=66000,
        )


def test_cross_field_configuration_valid_max_interval():
    """Verify WorkerSettings accepts configurations meeting max_domain_interval_ms formula."""
    settings = WorkerSettings(
        min_domain_interval_ms=1000,
        max_domain_interval_ms=60000,
        max_reservation_horizon_ms=60000,
        safety_margin_ms=5000,
        max_ttl_ms=130000,
    )
    assert settings.max_ttl_ms == 130000


def test_domain_validation_and_sha256_key():
    """Verify target domain identity validation and lowercase 64-char SHA-256 digest computation."""
    url1 = make_norm_url("www.example.com", registrable_domain="example.com")
    digest1 = derive_domain_digest(url1)
    assert len(digest1) == 64
    assert digest1.islower()

    # Apex and www produce identical registrable domain digest
    url2 = make_norm_url("example.com", registrable_domain="example.com")
    digest2 = derive_domain_digest(url2)
    assert digest1 == digest2

    # IPv4 literal
    url_ip = make_norm_url("93.184.216.34", host_type=HostType.IPV4)
    digest_ip = derive_domain_digest(url_ip)
    assert len(digest_ip) == 64

    # Reject empty or whitespace domain identity
    url_empty = make_norm_url("   ")
    with pytest.raises(InvalidDomainError, match="cannot be empty"):
        derive_domain_digest(url_empty)


@pytest.mark.anyio
async def test_redis_gate_reserved_flow():
    """Verify RESERVED status from Lua script sleeps caller for delay_ms."""
    settings = WorkerSettings()
    mock_redis = AsyncMock()
    mock_redis.script_load.return_value = "fake_sha"
    mock_redis.evalsha.return_value = ["RESERVED", "50", "1000"]

    slept_duration: list[float] = []

    async def fake_sleeper(seconds: float) -> None:
        slept_duration.append(seconds)

    gate = RedisDomainRequestGate(
        redis_client=mock_redis,
        settings=settings,
        async_sleeper=fake_sleeper,
    )

    url = make_norm_url("example.com", registrable_domain="example.com")
    await gate.acquire(url)

    assert mock_redis.evalsha.call_count == 1
    assert len(slept_duration) == 1
    assert slept_duration[0] == 0.05  # 50ms / 1000.0


@pytest.mark.anyio
async def test_redis_gate_defer_flow_retry():
    """Verify DEFER status from Lua script triggers sleep without ghost key."""
    settings = WorkerSettings()
    mock_redis = AsyncMock()
    mock_redis.script_load.return_value = "fake_sha"

    # First call returns DEFER 100ms, second call returns RESERVED 0ms
    mock_redis.evalsha.side_effect = [
        ["DEFER", "100", "1000"],
        ["RESERVED", "0", "1000"],
    ]

    slept_duration: list[float] = []

    async def fake_sleeper(seconds: float) -> None:
        slept_duration.append(seconds)

    gate = RedisDomainRequestGate(
        redis_client=mock_redis,
        settings=settings,
        async_sleeper=fake_sleeper,
    )

    url = make_norm_url("example.com", registrable_domain="example.com")
    await gate.acquire(url)

    assert mock_redis.evalsha.call_count == 2
    assert len(slept_duration) == 1
    assert slept_duration[0] == 0.1  # 100ms / 1000.0


@pytest.mark.anyio
async def test_strict_pause_degraded_mode():
    """Verify strict_pause mode raises RedisRateLimitPausedError when Redis client is None."""
    settings = WorkerSettings(redis_rate_limit_fallback_mode="strict_pause")
    gate = RedisDomainRequestGate(
        redis_client=None,
        settings=settings,
    )

    url = make_norm_url("example.com", registrable_domain="example.com")
    with pytest.raises(RedisRateLimitPausedError, match="strict_pause mode"):
        await gate.acquire(url)


@pytest.mark.anyio
async def test_single_worker_local_fallback():
    """Verify single_worker_local fallback uses local in-process DomainRequestGate with jitter."""
    settings = WorkerSettings(redis_rate_limit_fallback_mode="single_worker_local")
    slept_duration: list[float] = []

    async def fake_sleeper(seconds: float) -> None:
        slept_duration.append(seconds)

    gate = RedisDomainRequestGate(
        redis_client=None,
        settings=settings,
        async_sleeper=fake_sleeper,
    )

    url = make_norm_url("example.com", registrable_domain="example.com")
    await gate.acquire(url)

    # Local fallback executed successfully
    assert len(slept_duration) >= 1


@pytest.mark.anyio
async def test_sanitized_worker_exception_logging(caplog: pytest.LogCaptureFixture) -> None:
    """Verify worker Redis failure logs contain zero credentials or target URLs."""
    settings = WorkerSettings(redis_rate_limit_fallback_mode="single_worker_local")
    mock_redis = AsyncMock()
    mock_redis.script_load.side_effect = Exception(
        "Connection to redis://user:secret@host:6379 failed for https://target.com"
    )

    gate = RedisDomainRequestGate(
        redis_client=mock_redis,
        settings=settings,
    )

    url = make_norm_url("target.com", registrable_domain="target.com")
    with caplog.at_level(logging.WARNING):
        await gate.acquire(url)

    logs = caplog.text
    assert "secret" not in logs
    assert "target.com" not in logs
    assert "REDIS_GATE_FAILED" in logs
    assert "Exception" in logs
