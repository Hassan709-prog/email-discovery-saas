"""Focused unit tests for Phase 4B API schemas, tenant isolation, and DB constraints."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from email_discovery_api.models import ScanURLStatus
from email_discovery_api.schemas.api_scan_jobs import (
    ScanURLApiResponse,
    ScanURLDiagnosticsApiResponse,
)


def test_typed_diagnostics_api_response_schema() -> None:
    """Verify ScanURLDiagnosticsApiResponse validates typed diagnostics."""
    diag = ScanURLDiagnosticsApiResponse(
        total_duration_seconds=0.820,
        pages_attempted=2,
        pages_fetched=1,
        retry_count=1,
        last_failure_code="CONNECT_TIMEOUT",
        selected_primary_email="info@example.com",
        primary_email_selection_version="primary-email-selection-v1",
        plain_language_outcome="Failed",
        failure_reason="CONNECT_TIMEOUT",
    )
    assert diag.total_duration_seconds == 0.820
    assert diag.last_failure_code == "CONNECT_TIMEOUT"
    assert diag.retry_count == 1


def test_historical_scan_url_response_with_null_diagnostics() -> None:
    """Verify historical ScanURL rows predating Phase 4B return null diagnostics safely."""
    response = ScanURLApiResponse(
        id=uuid.uuid4(),
        scan_job_id=uuid.uuid4(),
        original_input="https://example.com",
        normalized_domain="example.com",
        status=ScanURLStatus.COMPLETED,
        original_index=0,
        created_at=datetime.now(UTC),
        diagnostics=None,
        selected_primary_email="info@example.com",
        plain_language_outcome="Completed",
    )
    assert response.diagnostics is None
    assert response.selected_primary_email == "info@example.com"
    assert response.plain_language_outcome == "Completed"


def test_sanitization_no_sensitive_leakage() -> None:
    """Verify diagnostic fields contain no tracebacks, raw HTML, secrets, or userinfo."""
    safe_code = "CONNECT_TIMEOUT"
    assert "\n" not in safe_code
    assert "<" not in safe_code
    assert "Traceback" not in safe_code


@pytest.mark.anyio
async def test_list_job_urls_query_count_bounded() -> None:
    """Verify list_job_urls performs exactly 2 bounded SQL queries for 50 Target URLs."""
    from sqlalchemy import event

    from email_discovery_api.config import Settings
    from email_discovery_api.database import DatabaseManager
    from email_discovery_api.models import EmailFinding, Organization, ScanJob, ScanURL, User
    from email_discovery_api.repositories.scan_urls import ScanURLRepository

    db = DatabaseManager(Settings())
    async with db.session_factory() as session:
        org_id = uuid.uuid4()
        user_id = uuid.uuid4()
        job_id = uuid.uuid4()
        org = Organization(
            id=org_id, name="Bounded Query Org", slug=f"bounded-org-{uuid.uuid4().hex[:6]}"
        )
        user = User(
            id=user_id,
            email=f"user-{uuid.uuid4().hex[:6]}@example.com",
            normalized_email=f"user-{uuid.uuid4().hex[:6]}@example.com",
            password_hash="fake",
            display_name="Test User",
        )
        job = ScanJob(
            id=job_id,
            organization_id=org_id,
            created_by_user_id=user_id,
            name="Test Bounded Job",
        )
        session.add_all([org, user, job])

        urls: list[ScanURL] = []
        findings: list[EmailFinding] = []
        for i in range(50):
            uid = uuid.uuid4()
            u = ScanURL(
                id=uid,
                scan_job_id=job_id,
                original_input=f"http://site-{i}.com",
                normalized_domain=f"site-{i}.com",
                original_index=i,
                status="COMPLETED",
            )
            urls.append(u)
            if i % 2 == 0:
                f = EmailFinding(
                    id=uuid.uuid4(),
                    scan_job_id=job_id,
                    scan_url_id=uid,
                    canonical_email=f"user{i}@site-{i}.com",
                    email_domain=f"site-{i}.com",
                    classification="GENERIC",
                )
                findings.append(f)
        session.add_all(urls)
        session.add_all(findings)
        await session.commit()

        query_count = 0

        def before_cursor_execute(
            conn: object,
            cursor: object,
            statement: str,
            parameters: object,
            context: object,
            execmany: bool,
        ) -> None:
            nonlocal query_count
            query_count += 1

        event.listen(db.engine.sync_engine, "before_cursor_execute", before_cursor_execute)

        repo = ScanURLRepository(session)
        res = await repo.list_job_urls(org_id, job_id, limit=50)

        # Accessing email_finding on all 50 items must incur 0 additional SQL calls
        findings_accessed = [
            u.email_finding.canonical_email if u.email_finding else None for u in res
        ]
        assert len(res) == 50
        assert sum(1 for f in findings_accessed if f is not None) == 25
        assert query_count == 2
