"""Authorization and privacy contract tests for private operations routes."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from email_discovery_api.api.dependencies.identity import RequestPrincipal, get_current_principal
from email_discovery_api.api.dependencies.services import get_operational_service
from email_discovery_api.config import Settings, get_settings
from email_discovery_api.main import create_app
from email_discovery_api.schemas.operations import (
    DependencyReadiness,
    JobOperationalMetrics,
    OperationalMetricsResponse,
    URLOperationalMetrics,
    WorkerOperationalMetrics,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
def test_app() -> FastAPI:
    """Create an operations route test app without starting external services."""
    return create_app(Settings())


def _metrics() -> OperationalMetricsResponse:
    return OperationalMetricsResponse(
        generated_at=datetime.now(UTC),
        window_seconds=300,
        readiness=DependencyReadiness(postgresql="ok", redis="ok", redis_required=False),
        workers=WorkerOperationalMetrics(
            present=0,
            stale=0,
            configured_concurrency=0,
            active_claims=0,
            states=[],
            records=[],
        ),
        urls=URLOperationalMetrics(
            queued=0,
            leased=0,
            scanning=0,
            retry_wait=0,
            completed=0,
            no_email=0,
            failed=0,
            expired_leases=0,
            retry_total=0,
            recent_terminal_count=0,
            recent_throughput_per_second=0,
            failure_reasons=[],
        ),
        jobs=JobOperationalMetrics(active=0, terminal=0),
    )


async def _get(app: FastAPI) -> tuple[int, dict[str, object]]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/operations/metrics")
    return response.status_code, response.json()


async def test_operations_disabled_by_default_even_with_authenticated_user(
    test_app: FastAPI,
) -> None:
    principal = RequestPrincipal(user_id=uuid4(), organization_id=uuid4())
    test_app.dependency_overrides[get_current_principal] = lambda: principal
    status_code, payload = await _get(test_app)
    assert status_code == 404
    assert payload["error"]["code"] == "NOT_FOUND"  # type: ignore[index]


async def test_tenant_owner_or_admin_role_does_not_grant_system_access(test_app: FastAPI) -> None:
    principal = RequestPrincipal(user_id=uuid4(), organization_id=uuid4())
    settings = Settings(operations_enabled=True, operations_admin_user_ids=str(uuid4()))
    test_app.dependency_overrides[get_settings] = lambda: settings
    test_app.dependency_overrides[get_current_principal] = lambda: principal
    status_code, _ = await _get(test_app)
    assert status_code == 403


async def test_explicit_allowlisted_user_receives_only_safe_typed_aggregates(
    test_app: FastAPI,
) -> None:
    user_id = uuid4()
    principal = RequestPrincipal(user_id=user_id, organization_id=uuid4())
    settings = Settings(operations_enabled=True, operations_admin_user_ids=str(user_id))
    service = AsyncMock()
    service.metrics.return_value = _metrics()
    test_app.dependency_overrides[get_settings] = lambda: settings
    test_app.dependency_overrides[get_current_principal] = lambda: principal
    test_app.dependency_overrides[get_operational_service] = lambda: service
    status_code, payload = await _get(test_app)
    assert status_code == 200
    serialized = str(payload).lower()
    for forbidden in (
        "organization_id",
        "job_id",
        "url_id",
        "normalized_url",
        "domain",
        "worker_label",
        "database_url",
        "redis_url",
    ):
        assert forbidden not in serialized
    for sensitive_value in (
        "https://target.example/private",
        "person@target.example",
        "postgresql+asyncpg://",
        "redis://",
    ):
        assert sensitive_value not in serialized
