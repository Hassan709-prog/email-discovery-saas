"""API integration and unit tests for Phase 4D Conservative Pre-Scan URL Cleaning and Review."""

import socket
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from email_discovery_api.api.dependencies.identity import RequestPrincipal, get_current_principal
from email_discovery_api.api.dependencies.services import get_scan_job_service
from email_discovery_api.main import create_app
from email_discovery_api.models.enums import ScanJobStatus
from email_discovery_api.models.scan_job import ScanJob
from email_discovery_api.schemas.scan_jobs import CreateScanJobCommand
from email_discovery_api.services.errors import ServiceError, ServiceErrorCode
from email_discovery_api.services.scan_jobs import (
    CreateJobResult,
    ScanJobService,
    compute_request_fingerprint,
)


@pytest.fixture
def test_app() -> FastAPI:
    """Construct FastAPI app instance for testing."""
    app = create_app()
    app.state.db_manager = MagicMock()
    return app


@pytest.fixture
def client(test_app: FastAPI) -> TestClient:
    """Create TestClient instance."""
    return TestClient(test_app)


@pytest.fixture
def test_principal() -> RequestPrincipal:
    """Default test request principal."""
    return RequestPrincipal(
        user_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        request_id="test-req-phase4d",
    )


def test_preview_url_cleaning_endpoint_no_network(
    test_app: FastAPI, client: TestClient, test_principal: RequestPrincipal
) -> None:
    """Test preview endpoint returns Phase 4D cleaning breakdown with 0 network calls."""
    test_app.dependency_overrides[get_current_principal] = lambda: test_principal

    payload = {
        "inputs": [
            "https://grandeurhillsgroup.com/",
            "https://www.google.com/search?q=home+builders",
            "https://www.archi-builders.com/",
            "https://archi-builders.com/",
            "http://93.184.216.34/",
            "not-a-valid-url",
        ]
    }

    http_client: Any = client
    with (
        patch.object(
            socket,
            "getaddrinfo",
            side_effect=AssertionError("Network DNS lookup attempted during preview!"),
        ),
        patch.object(
            socket,
            "create_connection",
            side_effect=AssertionError("Network connection attempted during preview!"),
        ),
    ):
        res = http_client.post("/api/v1/scan-jobs/preview", json=payload)

    assert res.status_code == 200
    data = res.json()

    assert data["total_input_count"] == 6
    assert data["ready_to_check_count"] == 2  # grandeurhills, archi-builders (1st)
    assert data["needs_review_count"] == 1  # 93.184.216.34 public IP
    assert data["unrelated_platform_count"] == 1  # google search
    assert data["duplicate_input_count"] == 1  # archi-builders 2nd
    assert data["invalid_input_count"] == 1  # not-a-valid-url
    assert data["final_target_count"] == 3  # 2 ready + 1 review = 3 targets

    accepted_targets = data["accepted_canonical_targets"]
    assert len(accepted_targets) == 3
    assert accepted_targets == [
        "https://grandeurhillsgroup.com/",
        "https://archi-builders.com/",
        "https://93.184.216.34/",
    ]


def test_create_scan_job_service_recomputes_cleaning_and_creates_urls_only_for_accepted_targets(
    test_app: FastAPI, client: TestClient, test_principal: RequestPrincipal
) -> None:
    """Verify create scan job recomputes cleaning decisions and returns accepted target counts."""
    mock_service = AsyncMock(spec=ScanJobService)
    test_app.dependency_overrides[get_current_principal] = lambda: test_principal
    test_app.dependency_overrides[get_scan_job_service] = lambda: mock_service

    job_id = uuid.uuid4()
    mock_job = ScanJob(
        id=job_id,
        organization_id=test_principal.organization_id,
        created_by_user_id=test_principal.user_id,
        name="Supplier Discovery",
        status=ScanJobStatus.DRAFT.value,
        source_type="MANUAL",
        scanner_version="1.0.0",
        normalization_version="1.0.0",
        ranking_version="1.0.0",
        configuration_snapshot={},
        total_input_count=6,
        valid_input_count=2,  # accepted targets
        duplicate_input_count=1,
        queued_count=0,
        running_count=0,
        completed_count=0,
        failed_count=0,
        email_finding_count=0,
    )
    mock_service.create_job.return_value = CreateJobResult(job=mock_job, created=True)

    payload = {
        "name": "Supplier Discovery",
        "inputs": [
            "https://grandeurhillsgroup.com/",
            "https://www.google.com/search?q=home+builders",
            "https://www.archi-builders.com/",
            "https://archi-builders.com/",
            "https://policies.google.com/privacy",
            "invalid-url-text",
        ],
    }

    http_client: Any = client
    res = http_client.post("/api/v1/scan-jobs", json=payload)
    assert res.status_code == 201
    job_data = res.json()

    assert job_data["total_input_count"] == 6
    assert job_data["valid_input_count"] == 2  # accepted targets count
    assert job_data["duplicate_input_count"] == 1
    assert job_data["invalid_input_count"] == 3  # 6 total - 2 valid - 1 dup = 3


def test_zero_accepted_targets_returns_http_400_error(
    test_app: FastAPI, client: TestClient, test_principal: RequestPrincipal
) -> None:
    """Verify job creation with zero eligible targets returns HTTP 400 error."""
    mock_service = AsyncMock(spec=ScanJobService)
    test_app.dependency_overrides[get_current_principal] = lambda: test_principal
    test_app.dependency_overrides[get_scan_job_service] = lambda: mock_service

    mock_service.create_job.side_effect = ServiceError(
        ServiceErrorCode.NO_VALID_INPUTS,
        "No eligible websites are ready to scan. Review the excluded and invalid inputs.",
    )

    payload = {
        "name": "Only Excluded Inputs",
        "inputs": [
            "https://www.google.com/search?q=builders",
            "https://policies.google.com/privacy",
            "invalid-url-text",
        ],
    }

    http_client: Any = client
    res = http_client.post("/api/v1/scan-jobs", json=payload)
    assert res.status_code == 400
    err_data = res.json()
    assert err_data["error"]["code"] == "NO_VALID_INPUTS"
    assert "No eligible websites are ready to scan" in err_data["error"]["message"]


def test_idempotency_fingerprint_based_on_accepted_canonical_targets() -> None:
    """Verify raw inputs yielding identical canonical targets share the same fingerprint."""
    org_id = uuid.uuid4()
    user_id = uuid.uuid4()

    cmd1 = CreateScanJobCommand(
        organization_id=org_id,
        created_by_user_id=user_id,
        inputs=[
            "https://www.archi-builders.com/?utm_source=test",
            "https://archi-builders.com/",
            "https://www.google.com/search?q=builders",
        ],
    )

    cmd2 = CreateScanJobCommand(
        organization_id=org_id,
        created_by_user_id=user_id,
        inputs=[
            "https://archi-builders.com/",
        ],
    )

    targets = ["https://archi-builders.com/"]

    fp1 = compute_request_fingerprint(cmd1, targets)
    fp2 = compute_request_fingerprint(cmd2, targets)

    assert fp1 == fp2
    assert len(fp1) == 64
