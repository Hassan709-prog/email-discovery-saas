"""Unit and integration tests for CSV export streaming and formula protection."""

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from email_discovery_api.api.dependencies.identity import RequestPrincipal, get_current_principal
from email_discovery_api.api.dependencies.services import get_session_factory
from email_discovery_api.main import app
from email_discovery_api.models import EmailFinding, Organization, ScanJob, User
from email_discovery_api.models.enums import ScanJobStatus
from email_discovery_api.services.results import sanitize_csv_cell


def test_sanitize_csv_cell_formula_protection() -> None:
    """Verify CSV cell sanitization order and formula injection protection."""
    # Normal values
    assert sanitize_csv_cell("user@example.com") == "user@example.com"
    assert sanitize_csv_cell(None) == ""
    assert sanitize_csv_cell(123) == "123"

    # Linebreaks and control characters replaced with space
    assert sanitize_csv_cell("line1\r\nline2\x07") == "line1  line2 "

    # Formula leading characters =, +, -, @ after lstrip
    assert sanitize_csv_cell("=SUM(A1:A10)") == "'=SUM(A1:A10)"
    assert sanitize_csv_cell("+12345") == "'+12345"
    assert sanitize_csv_cell("-12345") == "'-12345"
    assert sanitize_csv_cell("@admin") == "'@admin"

    # Leading spaces before formula character
    assert sanitize_csv_cell("   =cmd|' /C calc'!A0") == "'   =cmd|' /C calc'!A0"
    assert sanitize_csv_cell("\t-100") == "' -100"


@pytest.fixture
async def seeded_export_jobs(
    isolated_db_engine: AsyncEngine,
) -> AsyncGenerator[dict[str, Any]]:
    """Seed database with running and completed jobs for export tests."""
    session_factory = async_sessionmaker(
        bind=isolated_db_engine, expire_on_commit=False, class_=AsyncSession
    )
    db_manager = MagicMock()
    db_manager.session_factory = session_factory
    app.state.db_manager = db_manager

    org_id = uuid.uuid4()
    user_id = uuid.uuid4()
    principal = RequestPrincipal(
        user_id=user_id,
        organization_id=org_id,
        request_id="test-req-123",
    )
    app.dependency_overrides[get_current_principal] = lambda: principal
    app.dependency_overrides[get_session_factory] = lambda: session_factory

    running_job_id = uuid.uuid4()
    completed_job_id = uuid.uuid4()

    now = datetime.now(UTC)

    async with session_factory() as session:
        async with session.begin():
            org = Organization(id=org_id, name="Test Org", slug="test-org")
            user = User(
                id=user_id,
                email="user@test.com",
                normalized_email="user@test.com",
                password_hash="hash",
            )
            running_job = ScanJob(
                id=running_job_id,
                organization_id=org_id,
                created_by_user_id=user_id,
                status=ScanJobStatus.RUNNING.value,
                total_input_count=1,
                valid_input_count=1,
                queued_count=0,
                running_count=1,
            )
            completed_job = ScanJob(
                id=completed_job_id,
                organization_id=org_id,
                created_by_user_id=user_id,
                status=ScanJobStatus.COMPLETED.value,
                total_input_count=1,
                valid_input_count=1,
                completed_count=1,
                email_finding_count=1,
            )
            finding = EmailFinding(
                id=uuid.uuid4(),
                scan_job_id=completed_job_id,
                canonical_email="=cmd|' /c calc'!a0@example.com",
                email_domain="example.com",
                classification="ROLE_BASED",
                is_role_based=True,
                validation_status="UNVERIFIED",
                first_found_at=now,
                last_found_at=now,
                evidence_count=1,
            )
            session.add_all([org, user, running_job, completed_job, finding])

    yield {
        "session_factory": session_factory,
        "org_id": org_id,
        "running_job_id": running_job_id,
        "completed_job_id": completed_job_id,
    }
    app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_export_nonterminal_job_returns_409(
    seeded_export_jobs: dict[str, Any],
) -> None:
    """Verify attempting to export a RUNNING job returns HTTP 409 CONFLICT."""
    running_job_id = seeded_export_jobs["running_job_id"]

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        res = await client.get(f"/api/v1/scan-jobs/{running_job_id}/export.csv")
        assert res.status_code == 409
        assert res.json()["error"]["code"] == "INVALID_STATE_TRANSITION"


@pytest.mark.anyio
async def test_export_terminal_job_streams_clean_csv(
    seeded_export_jobs: dict[str, Any],
) -> None:
    """Verify exporting a COMPLETED job streams valid CSV with CRLF and UTF-8."""
    completed_job_id = seeded_export_jobs["completed_job_id"]

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        res = await client.get(f"/api/v1/scan-jobs/{completed_job_id}/export.csv")
        assert res.status_code == 200
        assert "text/csv" in res.headers["content-type"]
        assert "attachment; filename=" in res.headers["content-disposition"]

        content = res.text
        lines = content.split("\r\n")
        expected_header = (
            "scan_url_id,target_url,canonical_email,email_domain,classification,"
            "is_role_based,validation_status,evidence_count,"
            "first_found_at,last_found_at"
        )
        assert lines[0] == expected_header
        # Verify formula protection on row
        assert "''=cmd" in lines[1] or "'=cmd" in lines[1]


@pytest.mark.anyio
async def test_export_exceeding_max_rows_returns_409(
    seeded_export_jobs: dict[str, Any],
) -> None:
    """Verify export count > MAX_SYNC_EXPORT_ROWS returns 409 EXPORT_TOO_LARGE before streaming."""
    completed_job_id = seeded_export_jobs["completed_job_id"]

    # Patch MAX_SYNC_EXPORT_ROWS to 0 to trigger limit check
    from unittest.mock import patch

    with patch("email_discovery_api.services.results.MAX_SYNC_EXPORT_ROWS", 0):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            res = await client.get(f"/api/v1/scan-jobs/{completed_job_id}/export.csv")
            assert res.status_code == 409
            body = res.json()
            assert body["error"]["code"] == "EXPORT_TOO_LARGE"
            assert "exceeds the synchronous CSV export limit" in body["error"]["message"]


@pytest.mark.anyio
async def test_stream_export_batches_identity_map_isolation(
    seeded_export_jobs: dict[str, Any],
) -> None:
    """Verify stream_export_batches expunges ORM state and detaches objects."""
    from email_discovery_api.services.results import ScanJobResultsService

    session_factory = seeded_export_jobs["session_factory"]
    org_id = seeded_export_jobs["org_id"]
    completed_job_id = seeded_export_jobs["completed_job_id"]

    async with session_factory() as session:
        service = ScanJobResultsService(session)
        batches: list[list[EmailFinding]] = []
        async for batch in service.stream_export_batches(
            org_id, completed_job_id, batch_size=1, session_factory=session_factory
        ):
            batches.append(batch)
            for item in batch:
                assert item not in session
        assert len(session.identity_map) == 0
        assert len(batches) == 1
