"""Unit tests for API Redis client, queue job transition, and wake-up publisher."""

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from email_discovery_api.config import Settings
from email_discovery_api.redis import APIRedisClient, sanitize_redis_url
from email_discovery_api.services.scan_jobs import QueueJobResult


def test_sanitize_redis_url():
    """Verify passwords and credentials are masked in connection URLs."""
    raw = "redis://user:secret_password@localhost:6379/0"
    sanitized = sanitize_redis_url(raw)
    assert "secret_password" not in sanitized
    assert "user" not in sanitized
    assert sanitized == "redis://***@localhost:6379/0" or "***" in sanitized


def test_sanitize_redis_url_empty_or_no_auth():
    """Verify plain URLs without auth return unchanged."""
    assert sanitize_redis_url("") == ""
    assert sanitize_redis_url("redis://localhost:6379/0") == "redis://localhost:6379/0"


@pytest.mark.anyio
async def test_queue_job_result_dataclass():
    """Verify QueueJobResult returns job and transitioned_to_queued flag."""
    mock_job = MagicMock()
    res_queued = QueueJobResult(job=mock_job, transitioned_to_queued=True)
    assert res_queued.job is mock_job
    assert res_queued.transitioned_to_queued is True

    res_replay = QueueJobResult(job=mock_job, transitioned_to_queued=False)
    assert res_replay.transitioned_to_queued is False


@pytest.mark.anyio
async def test_api_redis_client_health_probe_caching(caplog: pytest.LogCaptureFixture) -> None:
    """Verify health check result is cached for 5 seconds using single-flight lock."""
    settings = Settings()
    client = APIRedisClient(settings)

    mock_redis = AsyncMock()
    mock_redis.ping.return_value = True
    client.client = mock_redis

    # First call triggers ping
    ok1 = await client.check_health()
    assert ok1 is True
    assert mock_redis.ping.call_count == 1

    # Second call within 5s uses cached status without calling ping again
    ok2 = await client.check_health()
    assert ok2 is True
    assert mock_redis.ping.call_count == 1


@pytest.mark.anyio
async def test_sanitized_exception_logging_no_secrets(caplog: pytest.LogCaptureFixture) -> None:
    """Verify Redis failure logs contain only error codes and class names, zero secrets."""
    settings = Settings()
    client = APIRedisClient(settings)
    mock_redis = AsyncMock()

    # Exception containing fake credentials and target URLs
    secret_exc = Exception(
        "Connection to redis://admin:supersecret@db.internal:6379/0 "
        "for target https://secret-domain.com failed"
    )
    mock_redis.publish.side_effect = secret_exc
    client.client = mock_redis
    client.is_available = True

    with caplog.at_level(logging.WARNING):
        await client.publish_work_available()

    logs = caplog.text
    assert "supersecret" not in logs
    assert "admin" not in logs
    assert "secret-domain.com" not in logs
    assert "REDIS_PUBLISH_FAILED" in logs
    assert "Exception" in logs
