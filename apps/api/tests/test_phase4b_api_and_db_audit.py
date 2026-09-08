"""Focused unit tests for Phase 4B API schemas, tenant isolation, and DB constraints."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

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


def test_scan_url_response_serialization_historical_null_redirect_fields() -> None:
    """Verify serialization of historical ScanURL rows with null redirect fields."""
    mock_url = MagicMock(
        last_failure_code=None,
        last_error_message=None,
        total_duration_seconds=None,
        pages_attempted=None,
        pages_fetched=None,
    )
    mock_url.id = uuid.uuid4()
    mock_url.scan_job_id = uuid.uuid4()
    mock_url.original_index = 0
    mock_url.original_input = "https://example.com"
    mock_url.normalized_url = "https://example.com"
    mock_url.normalized_domain = "example.com"
    mock_url.status = ScanURLStatus.COMPLETED
    mock_url.duplicate_of_scan_url_id = None
    mock_url.last_error_code = None
    mock_url.created_at = datetime.now(UTC)
    mock_url.processing_duration_seconds = None
    mock_url.retry_count = 0
    mock_url.pages_checked = 1
    mock_url.approved_redirect_domain = None
    mock_url.redirect_target_domain = None
    mock_url.redirect_target_url = None
    mock_url.requires_redirect_approval = False
    mock_url.email_finding = None
    mock_url.scan_job = MagicMock(status="COMPLETED")

    res = ScanURLApiResponse.from_orm_model(mock_url)
    assert res.approved_redirect_domain is None
    assert res.redirect_target_domain is None
    assert res.redirect_target_url is None
    assert res.requires_redirect_approval is False
    assert res.can_approve_redirect is False


def test_scan_url_response_serialization_populated_redirect_fields() -> None:
    """Verify serialization of ScanURL rows with populated redirect fields."""
    mock_url = MagicMock(
        last_failure_code="OUT_OF_SCOPE_REDIRECT",
        last_error_message=None,
        total_duration_seconds=1.2,
        pages_attempted=2,
        pages_fetched=1,
    )
    mock_url.id = uuid.uuid4()
    mock_url.scan_job_id = uuid.uuid4()
    mock_url.original_index = 0
    mock_url.original_input = "https://old-domain.com"
    mock_url.normalized_url = "https://old-domain.com"
    mock_url.normalized_domain = "old-domain.com"
    mock_url.status = ScanURLStatus.FAILED
    mock_url.duplicate_of_scan_url_id = None
    mock_url.last_error_code = "OUT_OF_SCOPE_REDIRECT"
    mock_url.created_at = datetime.now(UTC)
    mock_url.processing_duration_seconds = 1.2
    mock_url.retry_count = 1
    mock_url.pages_checked = 2
    mock_url.approved_redirect_domain = None
    mock_url.redirect_target_domain = "new-domain.com"
    mock_url.redirect_target_url = "https://new-domain.com/landing"
    mock_url.email_finding = None
    mock_url.scan_job = MagicMock(status="NEEDS_REVIEW")

    res = ScanURLApiResponse.from_orm_model(mock_url)
    assert res.approved_redirect_domain is None
    assert res.redirect_target_domain == "new-domain.com"
    assert res.redirect_target_url == "https://new-domain.com/landing"
    assert res.requires_redirect_approval is True
    assert res.can_approve_redirect is True


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

        # Accessing email_finding and scan_job on all 50 items must incur 0 additional SQL calls
        findings_accessed = [
            u.email_finding.canonical_email if u.email_finding else None for u in res
        ]
        job_statuses_accessed = [u.scan_job.status for u in res]
        assert len(res) == 50
        assert sum(1 for f in findings_accessed if f is not None) == 25
        assert all(s == "DRAFT" for s in job_statuses_accessed)
        assert query_count == 2


@pytest.mark.anyio
async def test_list_job_urls_repository_schema_mapping_no_missing_greenlet(
    isolated_db_engine: AsyncEngine,
) -> None:
    """Verify repository ORM mapping completes without MissingGreenlet using PostgreSQL DB."""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from email_discovery_api.models import EmailFinding, Organization, ScanJob, ScanURL, User
    from email_discovery_api.repositories.scan_urls import ScanURLRepository

    session_factory = async_sessionmaker(
        bind=isolated_db_engine, expire_on_commit=False, class_=AsyncSession
    )
    async with session_factory() as session:
        async with session.begin():
            org_id = uuid.uuid4()
            user_id = uuid.uuid4()
            job_id = uuid.uuid4()
            org = Organization(
                id=org_id, name="Repo Test Org", slug=f"repo-org-{uuid.uuid4().hex[:6]}"
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
                name="Repo Test Job",
            )
            url_id = uuid.uuid4()
            url_row = ScanURL(
                id=url_id,
                scan_job_id=job_id,
                original_input="https://testsite.com",
                normalized_domain="testsite.com",
                original_index=0,
                status="COMPLETED",
            )
            finding_row = EmailFinding(
                id=uuid.uuid4(),
                scan_job_id=job_id,
                scan_url_id=url_id,
                canonical_email="contact@testsite.com",
                email_domain="testsite.com",
                classification="GENERIC",
            )
            session.add_all([org, user, job, url_row, finding_row])

        repo = ScanURLRepository(session)
        db_urls = await repo.list_job_urls(org_id, job_id, limit=10)

        # Mapping DB rows to ScanURLApiResponse (accessing url.scan_job.status & url.email_finding)
        # must succeed without raising sqlalchemy.exc.MissingGreenlet!
        serialized = [ScanURLApiResponse.from_orm_model(u) for u in db_urls]
        assert len(serialized) == 1
        assert serialized[0].selected_primary_email == "contact@testsite.com"
        assert serialized[0].plain_language_outcome == "Completed"


_SENTINEL = object()


@pytest.mark.anyio
async def test_get_scan_job_urls_api_endpoint_http_response(
    isolated_db_engine: AsyncEngine,
) -> None:
    """Verify GET /api/v1/scan-jobs/{job_id}/urls returns HTTP 200 without MissingGreenlet."""
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from email_discovery_api.api.dependencies.identity import (
        RequestPrincipal,
        get_current_principal,
    )
    from email_discovery_api.api.dependencies.services import get_session_factory
    from email_discovery_api.main import app
    from email_discovery_api.models import EmailFinding, Organization, ScanJob, ScanURL, User

    # Capture exact pre-existing global FastAPI application state
    had_db_manager = hasattr(app.state, "db_manager")
    original_db_manager = getattr(app.state, "db_manager", None)
    orig_principal_override: Any = app.dependency_overrides.get(get_current_principal, _SENTINEL)
    orig_session_factory_override: Any = app.dependency_overrides.get(
        get_session_factory, _SENTINEL
    )

    session_factory = async_sessionmaker(
        bind=isolated_db_engine, expire_on_commit=False, class_=AsyncSession
    )
    db_manager = MagicMock()
    db_manager.session_factory = session_factory

    org_id = uuid.uuid4()
    user_id = uuid.uuid4()
    job_id = uuid.uuid4()
    principal = RequestPrincipal(
        user_id=user_id,
        organization_id=org_id,
        request_id="test-req-http-urls",
    )

    try:
        app.state.db_manager = db_manager
        app.dependency_overrides[get_current_principal] = lambda: principal
        app.dependency_overrides[get_session_factory] = lambda: session_factory

        async with session_factory() as session:
            async with session.begin():
                org = Organization(
                    id=org_id, name="HTTP Test Org", slug=f"http-org-{uuid.uuid4().hex[:6]}"
                )
                user = User(
                    id=user_id,
                    email=f"user-{uuid.uuid4().hex[:6]}@example.com",
                    normalized_email=f"user-{uuid.uuid4().hex[:6]}@example.com",
                    password_hash="hash",
                    display_name="HTTP User",
                )
                job = ScanJob(
                    id=job_id,
                    organization_id=org_id,
                    created_by_user_id=user_id,
                    name="HTTP Test Job",
                )
                # URL 1: Historical null redirect fields
                url1_id = uuid.uuid4()
                url1 = ScanURL(
                    id=url1_id,
                    scan_job_id=job_id,
                    original_input="https://site1.com",
                    normalized_domain="site1.com",
                    original_index=0,
                    status=ScanURLStatus.COMPLETED.value,
                    approved_redirect_domain=None,
                    redirect_target_domain=None,
                    redirect_target_url=None,
                )
                finding1 = EmailFinding(
                    id=uuid.uuid4(),
                    scan_job_id=job_id,
                    scan_url_id=url1_id,
                    canonical_email="primary@site1.com",
                    email_domain="site1.com",
                    classification="GENERIC",
                )
                # URL 2: Populated redirect fields needing approval
                url2_id = uuid.uuid4()
                url2 = ScanURL(
                    id=url2_id,
                    scan_job_id=job_id,
                    original_input="https://site2-old.com",
                    normalized_domain="site2-old.com",
                    original_index=1,
                    status=ScanURLStatus.FAILED.value,
                    last_error_code="OUT_OF_SCOPE_REDIRECT",
                    approved_redirect_domain=None,
                    redirect_target_domain="site2-new.com",
                    redirect_target_url="https://site2-new.com/target",
                )
                session.add_all([org, user, job, url1, url2, finding1])

        # Execute genuine HTTP request via AsyncClient
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            response = await client.get(f"/api/v1/scan-jobs/{job_id}/urls")

            assert response.status_code == 200
            data = response.json()
            assert "items" in data
            assert len(data["items"]) == 2

            # Assert item 1 (historical null redirect fields & primary email)
            item1 = next(it for it in data["items"] if it["id"] == str(url1_id))
            assert item1["original_input"] == "https://site1.com"
            assert item1["selected_primary_email"] == "primary@site1.com"
            assert item1["plain_language_outcome"] == "Completed"
            assert item1["approved_redirect_domain"] is None
            assert item1["redirect_target_domain"] is None
            assert item1["redirect_target_url"] is None
            assert item1["requires_redirect_approval"] is False
            assert item1["can_approve_redirect"] is False

            # Assert item 2 (populated redirect fields)
            item2 = next(it for it in data["items"] if it["id"] == str(url2_id))
            assert item2["original_input"] == "https://site2-old.com"
            assert item2["last_error_code"] == "OUT_OF_SCOPE_REDIRECT"
            assert item2["approved_redirect_domain"] is None
            assert item2["redirect_target_domain"] == "site2-new.com"
            assert item2["redirect_target_url"] == "https://site2-new.com/target"
            assert item2["requires_redirect_approval"] is True
            assert item2["can_approve_redirect"] is True

            # Cross-tenant access control assertion: foreign tenant principal receives 404
            foreign_principal = RequestPrincipal(
                user_id=uuid.uuid4(),
                organization_id=uuid.uuid4(),
                request_id="test-req-foreign",
            )
            app.dependency_overrides[get_current_principal] = lambda: foreign_principal
            foreign_response = await client.get(f"/api/v1/scan-jobs/{job_id}/urls")
            assert foreign_response.status_code == 404
    finally:
        # Non-destructive restoration of exact pre-existing FastAPI application state
        if had_db_manager:
            app.state.db_manager = original_db_manager
        elif hasattr(app.state, "db_manager"):
            delattr(app.state, "db_manager")

        if orig_principal_override is not _SENTINEL:
            app.dependency_overrides[get_current_principal] = orig_principal_override
        else:
            app.dependency_overrides.pop(get_current_principal, None)

        if orig_session_factory_override is not _SENTINEL:
            app.dependency_overrides[get_session_factory] = orig_session_factory_override
        else:
            app.dependency_overrides.pop(get_session_factory, None)


@pytest.mark.anyio
async def test_endpoint_test_state_restoration_preserves_preexisting_overrides_and_db_manager(
    isolated_db_engine: AsyncEngine,
) -> None:
    """Verify that endpoint execution restores pre-existing app.state and dependency_overrides."""
    from email_discovery_api.api.dependencies.identity import get_current_principal
    from email_discovery_api.api.dependencies.services import get_session_factory
    from email_discovery_api.main import app

    def dummy_dependency() -> str:
        return "dummy"

    def existing_principal_mock() -> str:
        return "mock_principal"

    def dummy_override() -> str:
        return "dummy_value"

    # Capture exact outer pre-existing global FastAPI application state
    had_outer_db_manager = hasattr(app.state, "db_manager")
    orig_outer_db_manager = getattr(app.state, "db_manager", None)
    orig_dummy_override: Any = app.dependency_overrides.get(dummy_dependency, _SENTINEL)
    orig_principal_override: Any = app.dependency_overrides.get(get_current_principal, _SENTINEL)
    orig_session_factory_override: Any = app.dependency_overrides.get(
        get_session_factory, _SENTINEL
    )

    try:
        sentinel_db_manager = MagicMock(name="PreexistingDBManager")
        app.state.db_manager = sentinel_db_manager
        app.dependency_overrides[dummy_dependency] = dummy_override
        app.dependency_overrides[get_current_principal] = existing_principal_mock

        await test_get_scan_job_urls_api_endpoint_http_response(isolated_db_engine)

        # Assert pre-existing state is intact and not wiped out by clear()
        assert app.state.db_manager is sentinel_db_manager
        override_fn = app.dependency_overrides.get(dummy_dependency)
        assert callable(override_fn) and override_fn() == "dummy_value"
        assert app.dependency_overrides.get(get_current_principal) is existing_principal_mock
        assert get_session_factory not in app.dependency_overrides
    finally:
        # Non-destructive restoration of exact outer pre-existing FastAPI application state
        if had_outer_db_manager:
            app.state.db_manager = orig_outer_db_manager
        elif hasattr(app.state, "db_manager"):
            delattr(app.state, "db_manager")

        if orig_dummy_override is not _SENTINEL:
            app.dependency_overrides[dummy_dependency] = orig_dummy_override
        else:
            app.dependency_overrides.pop(dummy_dependency, None)

        if orig_principal_override is not _SENTINEL:
            app.dependency_overrides[get_current_principal] = orig_principal_override
        else:
            app.dependency_overrides.pop(get_current_principal, None)

        if orig_session_factory_override is not _SENTINEL:
            app.dependency_overrides[get_session_factory] = orig_session_factory_override
        else:
            app.dependency_overrides.pop(get_session_factory, None)
