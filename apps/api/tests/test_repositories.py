"""Structural unit tests for repository SQL compilation, tenant scoping, and non-committing."""

import uuid
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from email_discovery_api.models.enums import ScanJobStatus
from email_discovery_api.repositories.job_events import JobEventRepository
from email_discovery_api.repositories.organizations import OrganizationAccessRepository
from email_discovery_api.repositories.scan_jobs import ScanJobRepository
from email_discovery_api.repositories.scan_urls import ScanURLRepository


def mock_session() -> tuple[AsyncSession, AsyncMock, MagicMock]:
    """Create a mock AsyncSession that tracks execute calls without real DB connection."""
    session = MagicMock(spec=AsyncSession)
    execute_mock = AsyncMock()
    result_mock = MagicMock()
    execute_mock.return_value = result_mock
    session.execute = execute_mock
    return session, execute_mock, result_mock


@pytest.mark.anyio
async def test_organization_repository_tenant_scoping() -> None:
    """Verify OrganizationAccessRepository compiles tenant-scoped locked queries."""
    session, exec_mock, _result_mock = mock_session()
    repo = OrganizationAccessRepository(session)
    org_id = uuid.uuid4()
    user_id = uuid.uuid4()

    await repo.get_active_organization_for_update(org_id)
    assert exec_mock.call_count == 1
    stmt_str = str(cast(Any, exec_mock.call_args)[0][0])
    assert "FROM organizations" in stmt_str
    assert "FOR UPDATE" in stmt_str

    await repo.get_active_membership(org_id, user_id)
    assert exec_mock.call_count == 2
    stmt_str = str(cast(Any, exec_mock.call_args)[0][0])
    assert "FROM memberships" in stmt_str
    assert "memberships.organization_id =" in stmt_str
    assert "memberships.user_id =" in stmt_str


@pytest.mark.anyio
async def test_scan_job_repository_tenant_scoping_and_atomic_sequence() -> None:
    """Verify ScanJobRepository scopes queries to org_id and compiles UPDATE ... RETURNING."""
    session, exec_mock, result_mock = mock_session()
    repo = ScanJobRepository(session)
    org_id = uuid.uuid4()
    job_id = uuid.uuid4()

    await repo.get_job(org_id, job_id)
    stmt_str = str(cast(Any, exec_mock.call_args)[0][0])
    assert "scan_jobs.organization_id =" in stmt_str
    assert "scan_jobs.id =" in stmt_str

    await repo.count_active_jobs(org_id)
    stmt_str = str(cast(Any, exec_mock.call_args)[0][0])
    assert "scan_jobs.organization_id =" in stmt_str
    assert "count(" in stmt_str.lower()

    result_mock.scalar_one_or_none.return_value = 1
    seq = await repo.allocate_event_sequence(org_id, job_id)
    assert seq == 1
    stmt_str = str(cast(Any, exec_mock.call_args)[0][0])
    assert "UPDATE scan_jobs SET next_event_sequence=" in stmt_str
    assert "RETURNING scan_jobs.next_event_sequence - " in stmt_str
    assert "scan_jobs.organization_id =" in stmt_str


@pytest.mark.anyio
async def test_scan_job_repository_conditional_update_tenant_scoped() -> None:
    """Verify status update contains org_id, job_id, and expected_status in WHERE clause."""
    session, exec_mock, result_mock = mock_session()
    repo = ScanJobRepository(session)
    org_id = uuid.uuid4()
    job_id = uuid.uuid4()

    result_mock.rowcount = 1
    result = await repo.update_job_status_conditional(
        org_id,
        job_id,
        expected_status=ScanJobStatus.DRAFT.value,
        new_status=ScanJobStatus.QUEUED.value,
    )
    assert result is True
    stmt_str = str(cast(Any, exec_mock.call_args)[0][0])
    assert "UPDATE scan_jobs SET status=" in stmt_str
    assert "scan_jobs.organization_id =" in stmt_str
    assert "scan_jobs.id =" in stmt_str
    assert "scan_jobs.status =" in stmt_str


@pytest.mark.anyio
async def test_scan_url_repository_join_tenant_scoping() -> None:
    """Verify ScanURLRepository joins ScanJob for tenant isolation and ordering."""
    session, exec_mock, result_mock = mock_session()
    repo = ScanURLRepository(session)
    org_id = uuid.uuid4()
    job_id = uuid.uuid4()

    result_mock.scalars.return_value.all.return_value = []
    await repo.list_job_urls(org_id, job_id)
    stmt_str = str(cast(Any, exec_mock.call_args)[0][0])
    assert "JOIN scan_jobs" in stmt_str
    assert "scan_jobs.organization_id =" in stmt_str
    assert "ORDER BY scan_urls.original_index ASC, scan_urls.id ASC" in stmt_str


@pytest.mark.anyio
async def test_job_event_repository_join_tenant_scoping() -> None:
    """Verify JobEventRepository joins ScanJob for tenant isolation and ordering."""
    session, exec_mock, result_mock = mock_session()
    repo = JobEventRepository(session)
    org_id = uuid.uuid4()
    job_id = uuid.uuid4()

    result_mock.scalars.return_value.all.return_value = []
    await repo.list_job_events(org_id, job_id)
    stmt_str = str(cast(Any, exec_mock.call_args)[0][0])
    assert "JOIN scan_jobs" in stmt_str
    assert "scan_jobs.organization_id =" in stmt_str
    assert "ORDER BY job_events.sequence_number ASC, job_events.id ASC" in stmt_str


def test_repositories_never_commit_or_rollback() -> None:
    """Verify repository classes do not expose commit or rollback calls."""
    for repo_cls in (
        OrganizationAccessRepository,
        ScanJobRepository,
        ScanURLRepository,
        JobEventRepository,
    ):
        for attr in dir(repo_cls):
            assert attr not in ("commit", "rollback")
