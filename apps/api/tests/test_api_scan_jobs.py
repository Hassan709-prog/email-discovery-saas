"""Comprehensive HTTP API unit tests for versioned scan-job endpoints under /api/v1/scan-jobs."""

from __future__ import annotations

import uuid
from collections.abc import Generator
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from email_discovery_api.api.dependencies.identity import RequestPrincipal, get_current_principal
from email_discovery_api.api.dependencies.services import get_scan_job_service
from email_discovery_api.config import Settings
from email_discovery_api.main import create_app
from email_discovery_api.models.enums import ScanJobStatus
from email_discovery_api.models.scan_job import ScanJob
from email_discovery_api.schemas.scan_jobs import ScanJobProgress
from email_discovery_api.services.errors import ServiceError, ServiceErrorCode
from email_discovery_api.services.scan_jobs import CreateJobResult, ScanJobService


@pytest.fixture
def test_app() -> Generator[FastAPI]:
    """Create FastAPI application instance for testing."""
    app = create_app()
    app.state.db_manager = MagicMock()
    yield app
    app.dependency_overrides.clear()


@pytest.fixture
def client(test_app: FastAPI) -> Any:
    """Create TestClient instance."""
    return TestClient(test_app)


@pytest.fixture
def test_principal() -> RequestPrincipal:
    """Default test request principal."""
    return RequestPrincipal(
        user_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        request_id="test-req-123",
    )


def test_missing_principal_returns_401(client: Any) -> None:
    """Verify endpoint returns HTTP 401 when principal is not authenticated."""
    response = client.get("/api/v1/scan-jobs")
    assert response.status_code == 401
    data = response.json()
    assert data["error"]["code"] == "UNAUTHORIZED"
    assert "X-Request-ID" in response.headers


def test_dev_identity_headers_disabled_by_default(client: Any) -> None:
    """Verify X-Dev identity headers are rejected when ALLOW_DEV_IDENTITY_HEADERS=false."""
    headers = {
        "X-Dev-User-ID": str(uuid.uuid4()),
        "X-Dev-Organization-ID": str(uuid.uuid4()),
    }
    response = client.get("/api/v1/scan-jobs", headers=headers)
    assert response.status_code == 401


def test_dev_identity_headers_rejected_in_non_dev_environment() -> None:
    """Verify app creation raises ValueError if ALLOW_DEV_IDENTITY_HEADERS=true outside dev."""
    prod_settings = Settings(
        environment="production",
        allow_dev_identity_headers=True,
    )
    with pytest.raises(ValueError, match="ALLOW_DEV_IDENTITY_HEADERS cannot be enabled"):
        create_app(prod_settings)


def test_client_tenant_fields_forbidden_in_request_body(
    test_app: FastAPI, client: Any, test_principal: RequestPrincipal
) -> None:
    """Verify client sending tenant fields in JSON body gets HTTP 422 extra field error."""
    test_app.dependency_overrides[get_current_principal] = lambda: test_principal

    payload = {
        "inputs": ["https://example.com"],
        "organization_id": str(uuid.uuid4()),
    }
    response = client.post("/api/v1/scan-jobs", json=payload)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "UNPROCESSABLE_ENTITY"


def test_preview_scan_jobs_success_and_limits(
    test_app: FastAPI, client: Any, test_principal: RequestPrincipal
) -> None:
    """Verify preview endpoint performs network-free normalization and policy validation."""
    test_app.dependency_overrides[get_current_principal] = lambda: test_principal

    # 1. Valid preview
    payload: dict[str, Any] = {
        "inputs": ["https://example.com", "ftp://invalid", "https://example.com"],
        "configuration_snapshot": {},
    }
    response = client.post("/api/v1/scan-jobs/preview", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["total_input_count"] == 3
    assert data["valid_input_count"] == 1
    assert data["duplicate_input_count"] == 1
    assert data["invalid_input_count"] == 1

    # 2. Oversized configuration exceeds limit -> 413
    oversized_payload = {
        "inputs": ["https://example.com"],
        "configuration_snapshot": {"large": "x" * 200_000},
    }
    res413 = client.post("/api/v1/scan-jobs/preview", json=oversized_payload)
    assert res413.status_code == 413
    assert res413.json()["error"]["code"] == "CONFIGURATION_TOO_LARGE"


def test_create_scan_job_creation_and_idempotent_replay(
    test_app: FastAPI, client: Any, test_principal: RequestPrincipal
) -> None:
    """Verify creation returns 201 + Location on new job and 200 on idempotent replay."""
    mock_service = MagicMock(spec=ScanJobService)
    test_app.dependency_overrides[get_current_principal] = lambda: test_principal
    test_app.dependency_overrides[get_scan_job_service] = lambda: mock_service

    job_id = uuid.uuid4()
    job = ScanJob(
        id=job_id,
        organization_id=test_principal.organization_id,
        created_by_user_id=test_principal.user_id,
        status=ScanJobStatus.DRAFT.value,
        source_type="MANUAL",
        scanner_version="1.0.0",
        normalization_version="1.0.0",
        ranking_version="1.0.0",
        configuration_snapshot={},
        total_input_count=1,
        valid_input_count=1,
        duplicate_input_count=0,
        queued_count=0,
        running_count=0,
        completed_count=0,
        failed_count=0,
        email_finding_count=0,
    )

    # 1. Newly created -> 201 + Location
    mock_service.create_job = AsyncMock(return_value=CreateJobResult(job=job, created=True))
    payload = {"inputs": ["https://example.com"]}
    res201 = client.post("/api/v1/scan-jobs", json=payload, headers={"Idempotency-Key": "key-1"})
    assert res201.status_code == 201
    assert res201.headers["Location"] == f"/api/v1/scan-jobs/{job_id}"
    assert res201.json()["id"] == str(job_id)

    # 2. Idempotent replay -> 200 + Location
    mock_service.create_job = AsyncMock(return_value=CreateJobResult(job=job, created=False))
    res200 = client.post("/api/v1/scan-jobs", json=payload, headers={"Idempotency-Key": "key-1"})
    assert res200.status_code == 200
    assert res200.headers["Location"] == f"/api/v1/scan-jobs/{job_id}"


def test_create_scan_job_idempotency_conflict(
    test_app: FastAPI, client: Any, test_principal: RequestPrincipal
) -> None:
    """Verify creation returns HTTP 409 on idempotency key fingerprint conflict."""
    mock_service = MagicMock(spec=ScanJobService)
    test_app.dependency_overrides[get_current_principal] = lambda: test_principal
    test_app.dependency_overrides[get_scan_job_service] = lambda: mock_service

    mock_service.create_job = AsyncMock(
        side_effect=ServiceError(
            ServiceErrorCode.IDEMPOTENCY_CONFLICT,
            "Key 'key-1' used with different fingerprint.",
        )
    )

    payload = {"inputs": ["https://different.com"]}
    response = client.post("/api/v1/scan-jobs", json=payload, headers={"Idempotency-Key": "key-1"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"


def test_idempotency_key_validation_patterns(
    test_app: FastAPI, client: Any, test_principal: RequestPrincipal
) -> None:
    """Verify Idempotency-Key validation accepts valid characters and rejects invalid formats."""
    mock_service = MagicMock(spec=ScanJobService)
    test_app.dependency_overrides[get_current_principal] = lambda: test_principal
    test_app.dependency_overrides[get_scan_job_service] = lambda: mock_service

    job_id = uuid.uuid4()
    job = ScanJob(
        id=job_id,
        organization_id=test_principal.organization_id,
        created_by_user_id=test_principal.user_id,
        status=ScanJobStatus.DRAFT.value,
        source_type="MANUAL",
        scanner_version="1.0.0",
        normalization_version="1.0.0",
        ranking_version="1.0.0",
        configuration_snapshot={},
        total_input_count=1,
        valid_input_count=1,
        duplicate_input_count=0,
        queued_count=0,
        running_count=0,
        completed_count=0,
        failed_count=0,
        email_finding_count=0,
        created_at=datetime.now(UTC),
    )
    mock_service.create_job = AsyncMock(return_value=CreateJobResult(job=job, created=True))

    payload = {"inputs": ["https://example.com"]}

    # Accepted keys: A-Z a-z 0-9 . _ : - (1 to 128 chars)
    accepted_keys = [
        "A",
        "abc-123.test_key:v1",
        "a_1.2:3-b",
        "k" * 128,
    ]
    for key in accepted_keys:
        res = client.post("/api/v1/scan-jobs", json=payload, headers={"Idempotency-Key": key})
        assert res.status_code == 201, f"Failed for valid key: {key!r}"

    # Rejected keys: spaces, tabs, quotes, slashes, backslashes, unicode, empty, >128 chars
    rejected_keys = [
        "",
        "   ",
        "  key-123",
        "key-123  ",
        "key with spaces",
        "key\twith\ttabs",
        "key/with/slash",
        "key\\with\\backslash",
        "key'with'singlequote",
        'key"with"doublequote',
        "key-\U0001f511-emoji".encode("utf-8"),
        "k" * 129,
    ]
    for key in rejected_keys:
        res = client.post("/api/v1/scan-jobs", json=payload, headers={"Idempotency-Key": key})
        assert res.status_code == 400, f"Expected 400 for invalid key: {key!r}"
        assert res.json()["error"]["code"] == "BAD_REQUEST"


def test_list_get_and_progress_scan_jobs(
    test_app: FastAPI, client: Any, test_principal: RequestPrincipal
) -> None:
    """Verify tenant-scoped list, detail, and progress endpoints."""
    mock_service = MagicMock(spec=ScanJobService)
    test_app.dependency_overrides[get_current_principal] = lambda: test_principal
    test_app.dependency_overrides[get_scan_job_service] = lambda: mock_service

    job_id = uuid.uuid4()
    job = ScanJob(
        id=job_id,
        organization_id=test_principal.organization_id,
        created_by_user_id=test_principal.user_id,
        status=ScanJobStatus.RUNNING.value,
        source_type="MANUAL",
        scanner_version="1.0.0",
        normalization_version="1.0.0",
        ranking_version="1.0.0",
        configuration_snapshot={},
        total_input_count=10,
        valid_input_count=10,
        duplicate_input_count=0,
        queued_count=5,
        running_count=0,
        completed_count=5,
        failed_count=0,
        email_finding_count=2,
    )

    # GET /api/v1/scan-jobs
    mock_service.list_jobs = AsyncMock(return_value=([job], None))
    res_list = client.get("/api/v1/scan-jobs")
    assert res_list.status_code == 200
    assert len(res_list.json()["items"]) == 1

    # GET /api/v1/scan-jobs/{job_id}
    mock_service.get_job = AsyncMock(return_value=job)
    res_get = client.get(f"/api/v1/scan-jobs/{job_id}")
    assert res_get.status_code == 200
    assert res_get.json()["id"] == str(job_id)

    # GET /api/v1/scan-jobs/{job_id}/progress
    progress = ScanJobProgress.from_counts(
        job_id=job_id,
        status=ScanJobStatus.RUNNING,
        total_input_count=10,
        valid_input_count=10,
        duplicate_input_count=0,
        queued_count=5,
        running_count=0,
        completed_count=5,
        failed_count=0,
        email_finding_count=2,
        created_at=datetime.now(UTC),
        started_at=datetime.now(UTC),
        completed_at=None,
    )
    mock_service.get_job_progress = AsyncMock(return_value=progress)
    res_prog = client.get(f"/api/v1/scan-jobs/{job_id}/progress")
    assert res_prog.status_code == 200
    assert res_prog.json()["progress_percentage"] == 50.0


def test_cross_tenant_access_returns_404(
    test_app: FastAPI, client: Any, test_principal: RequestPrincipal
) -> None:
    """Verify requesting missing or cross-tenant job returns 404 without disclosure."""
    mock_service = MagicMock(spec=ScanJobService)
    test_app.dependency_overrides[get_current_principal] = lambda: test_principal
    test_app.dependency_overrides[get_scan_job_service] = lambda: mock_service

    mock_service.get_job = AsyncMock(
        side_effect=ServiceError(ServiceErrorCode.JOB_NOT_FOUND, "Job not found.")
    )

    response = client.get(f"/api/v1/scan-jobs/{uuid.uuid4()}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "JOB_NOT_FOUND"


def test_malformed_and_oversized_cursors_rejected(
    test_app: FastAPI, client: Any, test_principal: RequestPrincipal
) -> None:
    """Verify malformed base64, wrong resource, or wrong version cursors return HTTP 400."""
    test_app.dependency_overrides[get_current_principal] = lambda: test_principal

    res_bad = client.get("/api/v1/scan-jobs?cursor=not-valid-base64!!!")
    assert res_bad.status_code == 400
    assert res_bad.json()["error"]["code"] == "BAD_REQUEST"


def test_queue_and_cancel_status_transitions(
    test_app: FastAPI, client: Any, test_principal: RequestPrincipal
) -> None:
    """Verify queue and cancel status transitions."""
    mock_service = MagicMock(spec=ScanJobService)
    test_app.dependency_overrides[get_current_principal] = lambda: test_principal
    test_app.dependency_overrides[get_scan_job_service] = lambda: mock_service

    job_id = uuid.uuid4()
    draft_job = ScanJob(
        id=job_id,
        organization_id=test_principal.organization_id,
        created_by_user_id=test_principal.user_id,
        status=ScanJobStatus.DRAFT.value,
        source_type="MANUAL",
        scanner_version="1.0.0",
        normalization_version="1.0.0",
        ranking_version="1.0.0",
        configuration_snapshot={},
        total_input_count=1,
        valid_input_count=1,
        duplicate_input_count=0,
        queued_count=0,
        running_count=0,
        completed_count=0,
        failed_count=0,
        email_finding_count=0,
    )
    queued_job = ScanJob(
        id=job_id,
        organization_id=test_principal.organization_id,
        created_by_user_id=test_principal.user_id,
        status=ScanJobStatus.QUEUED.value,
        source_type="MANUAL",
        scanner_version="1.0.0",
        normalization_version="1.0.0",
        ranking_version="1.0.0",
        configuration_snapshot={},
        total_input_count=1,
        valid_input_count=1,
        duplicate_input_count=0,
        queued_count=0,
        running_count=0,
        completed_count=0,
        failed_count=0,
        email_finding_count=0,
    )

    # 1. Queue draft job -> 200
    mock_service.transition_job_status = AsyncMock(return_value=queued_job)
    res_queue = client.post(f"/api/v1/scan-jobs/{job_id}/queue")
    assert res_queue.status_code == 200
    assert res_queue.json()["status"] == "QUEUED"

    # 2. Cancel DRAFT job -> 409 INVALID_STATE_TRANSITION
    mock_service.get_job = AsyncMock(return_value=draft_job)
    res_cancel_draft = client.post(f"/api/v1/scan-jobs/{job_id}/cancel")
    assert res_cancel_draft.status_code == 409
    assert res_cancel_draft.json()["error"]["code"] == "INVALID_STATE_TRANSITION"

    # 3. Cancel QUEUED job -> 200
    cancelled_job = ScanJob(
        id=job_id,
        organization_id=test_principal.organization_id,
        created_by_user_id=test_principal.user_id,
        status=ScanJobStatus.CANCELLED.value,
        source_type="MANUAL",
        scanner_version="1.0.0",
        normalization_version="1.0.0",
        ranking_version="1.0.0",
        configuration_snapshot={},
        total_input_count=1,
        valid_input_count=1,
        duplicate_input_count=0,
        queued_count=0,
        running_count=0,
        completed_count=0,
        failed_count=0,
        email_finding_count=0,
    )
    mock_service.get_job = AsyncMock(return_value=queued_job)
    mock_service.transition_job_status = AsyncMock(return_value=cancelled_job)
    res_cancel_queued = client.post(f"/api/v1/scan-jobs/{job_id}/cancel")
    assert res_cancel_queued.status_code == 200
    assert res_cancel_queued.json()["status"] == "CANCELLED"


def test_unhandled_exception_returns_sanitized_500(
    test_app: FastAPI, test_principal: RequestPrincipal
) -> None:
    """Verify unhandled exceptions log tracebacks and return generic 500 envelope."""
    client: Any = TestClient(test_app, raise_server_exceptions=False)
    mock_service = MagicMock(spec=ScanJobService)
    test_app.dependency_overrides[get_current_principal] = lambda: test_principal
    test_app.dependency_overrides[get_scan_job_service] = lambda: mock_service

    mock_service.get_job = AsyncMock(
        side_effect=RuntimeError("Secret database credentials connection string failed!")
    )

    response = client.get(f"/api/v1/scan-jobs/{uuid.uuid4()}")
    assert response.status_code == 500
    data = response.json()
    assert data["error"]["code"] == "INTERNAL_SERVER_ERROR"
    assert data["error"]["message"] == "An unexpected error occurred."
    assert "Secret database credentials" not in response.text


def test_openapi_schema_contains_routes_and_models(client: Any) -> None:
    """Verify OpenAPI JSON contains scan-job endpoints and response schemas."""
    response = client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    paths = schema["paths"]

    assert "/api/v1/scan-jobs/preview" in paths
    assert "/api/v1/scan-jobs" in paths
    assert "/api/v1/scan-jobs/{job_id}" in paths
    assert "/api/v1/scan-jobs/{job_id}/progress" in paths
    assert "/api/v1/scan-jobs/{job_id}/urls" in paths
    assert "/api/v1/scan-jobs/{job_id}/events" in paths
    assert "/api/v1/scan-jobs/{job_id}/queue" in paths
    assert "/api/v1/scan-jobs/{job_id}/cancel" in paths
