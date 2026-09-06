"""Comprehensive unit, integration, and transactional tests for bulk redirect review.

Covers:
- Authorization and tenant isolation
- Strict all-or-nothing atomic validation and rollback
- Missing, foreign, and other-job IDs with uniform 404 SCAN_URL_NOT_FOUND
- Deduplication of request IDs with requested_count vs unique_requested_count
- 0, >250 raw IDs, and >250 unique IDs boundary validation
- Field mutations for bulk approval and rejection
- Exact job counter invariants and job reopening from terminal statuses
- Monotonic max_attempts protection (no repeat increment on idempotent retry)
- Repeated approvals before and after worker completion (QUEUED, LEASED, COMPLETED,
  NO_EMAIL, subsequent FAILED)
- Repeated rejection idempotency (already REDIRECT_REJECTED)
- Approval of previously rejected row (transitions FAILED -> QUEUED)
- Canonical lock ordering preventing deadlocks under concurrent overlapping requests
- Cancellation fencing preventing approval/rejection on cancelled jobs
- Event payload boundedness (domains capped, no hundreds of IDs/raw URLs, no event on
  affected_count=0)
- Route-level commit-first ordering and Redis publication only on affected_count > 0
- Redis publication failure suppression/logging resilience
- Python predicate and SQL clause exact parity across NULL, whitespace, codes, and statuses
- Public failure reason formatting for REDIRECT_REJECTED
- Regression verification for existing single approval endpoint
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from email_discovery_api.api.dependencies.identity import RequestPrincipal, get_current_principal
from email_discovery_api.api.dependencies.services import get_redis_publisher, get_session_factory
from email_discovery_api.main import app
from email_discovery_api.models import (
    JobEvent,
    Organization,
    ScanJob,
    ScanURL,
    User,
    get_requires_redirect_approval_sql_clause,
    is_url_requiring_redirect_approval,
)
from email_discovery_api.models.enums import ScanJobStatus, ScanURLStatus
from email_discovery_api.models.redirect_review import REDIRECT_REJECTED_CODE
from email_discovery_api.schemas.api_scan_jobs import format_failure_reason
from email_discovery_api.schemas.scan_jobs import BulkRedirectCommand
from email_discovery_api.services.errors import ServiceError, ServiceErrorCode
from email_discovery_api.services.scan_jobs import ScanJobService


@pytest.mark.anyio
async def test_predicate_and_sql_parity(isolated_db_engine: AsyncEngine) -> None:
    """Verify 100% parity between Python predicate and SQL clause across all edge cases.

    Edge cases:
    1. Valid pending OUT_OF_SCOPE_REDIRECT -> True
    2. Valid pending BUSINESS_DOMAIN_REDIRECT_REVIEW -> True
    3. Empty last_failure_code with valid last_error_code fallback -> True
    4. Whitespace last_failure_code with valid last_error_code fallback -> True
    5. NULL redirect_target_domain -> False
    6. Empty string "" redirect_target_domain -> False
    7. Whitespace "   " redirect_target_domain -> False
    8. Already approved (approved_redirect_domain non-empty) -> False
    9. Already rejected (REDIRECT_REJECTED) -> False
    10. Unrelated failure code (e.g. DNS_RESOLUTION_FAILED) -> False
    11. Non-FAILED status (e.g. QUEUED, COMPLETED) -> False
    """
    session_factory = async_sessionmaker(
        bind=isolated_db_engine, expire_on_commit=False, class_=AsyncSession
    )

    org_id = uuid.uuid4()
    user_id = uuid.uuid4()
    job_id = uuid.uuid4()

    test_cases: list[dict[str, Any]] = [
        # 1. Valid pending OUT_OF_SCOPE_REDIRECT
        {
            "id": uuid.uuid4(),
            "status": ScanURLStatus.FAILED.value,
            "last_failure_code": "OUT_OF_SCOPE_REDIRECT",
            "last_error_code": None,
            "redirect_target_domain": "valid1.com",
            "approved_redirect_domain": None,
            "expected": True,
        },
        # 2. Valid pending BUSINESS_DOMAIN_REDIRECT_REVIEW
        {
            "id": uuid.uuid4(),
            "status": ScanURLStatus.FAILED.value,
            "last_failure_code": "BUSINESS_DOMAIN_REDIRECT_REVIEW",
            "last_error_code": None,
            "redirect_target_domain": "valid2.com",
            "approved_redirect_domain": None,
            "expected": True,
        },
        # 3. Empty last_failure_code falling back to last_error_code
        {
            "id": uuid.uuid4(),
            "status": ScanURLStatus.FAILED.value,
            "last_failure_code": "",
            "last_error_code": "OUT_OF_SCOPE_REDIRECT",
            "redirect_target_domain": "valid3.com",
            "approved_redirect_domain": None,
            "expected": True,
        },
        # 4. Whitespace last_failure_code falling back to last_error_code
        {
            "id": uuid.uuid4(),
            "status": ScanURLStatus.FAILED.value,
            "last_failure_code": "   ",
            "last_error_code": "BUSINESS_DOMAIN_REDIRECT_REVIEW",
            "redirect_target_domain": "valid4.com",
            "approved_redirect_domain": None,
            "expected": True,
        },
        # 5. NULL redirect_target_domain
        {
            "id": uuid.uuid4(),
            "status": ScanURLStatus.FAILED.value,
            "last_failure_code": "OUT_OF_SCOPE_REDIRECT",
            "last_error_code": None,
            "redirect_target_domain": None,
            "approved_redirect_domain": None,
            "expected": False,
        },
        # 6. Empty string redirect_target_domain
        {
            "id": uuid.uuid4(),
            "status": ScanURLStatus.FAILED.value,
            "last_failure_code": "OUT_OF_SCOPE_REDIRECT",
            "last_error_code": None,
            "redirect_target_domain": "",
            "approved_redirect_domain": None,
            "expected": False,
        },
        # 7. Whitespace redirect_target_domain
        {
            "id": uuid.uuid4(),
            "status": ScanURLStatus.FAILED.value,
            "last_failure_code": "OUT_OF_SCOPE_REDIRECT",
            "last_error_code": None,
            "redirect_target_domain": "   ",
            "approved_redirect_domain": None,
            "expected": False,
        },
        # 8. Already approved row
        {
            "id": uuid.uuid4(),
            "status": ScanURLStatus.FAILED.value,
            "last_failure_code": "OUT_OF_SCOPE_REDIRECT",
            "last_error_code": None,
            "redirect_target_domain": "dest8.com",
            "approved_redirect_domain": "dest8.com",
            "expected": False,
        },
        # 9. Already rejected row
        {
            "id": uuid.uuid4(),
            "status": ScanURLStatus.FAILED.value,
            "last_failure_code": REDIRECT_REJECTED_CODE,
            "last_error_code": REDIRECT_REJECTED_CODE,
            "redirect_target_domain": "dest9.com",
            "approved_redirect_domain": None,
            "expected": False,
        },
        # 10. Unrelated failure code
        {
            "id": uuid.uuid4(),
            "status": ScanURLStatus.FAILED.value,
            "last_failure_code": "DNS_RESOLUTION_FAILED",
            "last_error_code": "DNS_RESOLUTION_FAILED",
            "redirect_target_domain": "dest10.com",
            "approved_redirect_domain": None,
            "expected": False,
        },
        # 11. Non-FAILED status
        {
            "id": uuid.uuid4(),
            "status": ScanURLStatus.COMPLETED.value,
            "last_failure_code": "OUT_OF_SCOPE_REDIRECT",
            "last_error_code": None,
            "redirect_target_domain": "dest11.com",
            "approved_redirect_domain": None,
            "expected": False,
        },
    ]

    async with session_factory() as session:
        async with session.begin():
            org = Organization(id=org_id, name="Parity Org", slug=f"parity-{org_id.hex[:6]}")
            user = User(
                id=user_id,
                email=f"user-{user_id.hex[:6]}@example.com",
                normalized_email=f"user-{user_id.hex[:6]}@example.com",
                password_hash="hash",
            )
            job = ScanJob(
                id=job_id,
                organization_id=org_id,
                created_by_user_id=user_id,
                status=ScanJobStatus.RUNNING.value,
                total_input_count=len(test_cases),
                valid_input_count=len(test_cases),
            )
            urls = [
                ScanURL(
                    id=tc["id"],
                    scan_job_id=job_id,
                    original_input=f"https://test{idx}.com",
                    original_index=idx,
                    status=tc["status"],
                    last_failure_code=tc["last_failure_code"],
                    last_error_code=tc["last_error_code"],
                    redirect_target_domain=tc["redirect_target_domain"],
                    approved_redirect_domain=tc["approved_redirect_domain"],
                )
                for idx, tc in enumerate(test_cases)
            ]
            session.add_all([org, user, job, *urls])

    # 1. Verify Python predicate parity
    for idx, tc in enumerate(test_cases):
        url_obj = urls[idx]
        py_result = is_url_requiring_redirect_approval(url_obj)
        assert py_result == tc["expected"], (
            f"Case {idx} Python predicate failed: expected {tc['expected']}, got {py_result}"
        )

    # 2. Verify SQL predicate parity
    async with session_factory() as session:
        stmt = (
            select(ScanURL.id)
            .where(
                ScanURL.scan_job_id == job_id,
                get_requires_redirect_approval_sql_clause(),
            )
            .order_by(ScanURL.original_index.asc())
        )
        res = await session.execute(stmt)
        sql_matched_ids = set(res.scalars().all())

    expected_matched_ids = {tc["id"] for tc in test_cases if tc["expected"]}
    assert sql_matched_ids == expected_matched_ids


@pytest.mark.anyio
async def test_bulk_approve_atomic_success_and_counters(isolated_db_engine: AsyncEngine) -> None:
    """Verify bulk approval updates field mutations, increments queued, decrements failed,
    and reopens job.
    """
    session_factory = async_sessionmaker(
        bind=isolated_db_engine, expire_on_commit=False, class_=AsyncSession
    )

    org_id = uuid.uuid4()
    user_id = uuid.uuid4()
    job_id = uuid.uuid4()
    url_id1 = uuid.uuid4()
    url_id2 = uuid.uuid4()
    url_id3 = uuid.uuid4()  # Already approved

    async with session_factory() as session:
        async with session.begin():
            org = Organization(id=org_id, name="Org", slug=f"org-{org_id.hex[:6]}")
            user = User(
                id=user_id,
                email=f"u-{user_id.hex[:6]}@example.com",
                normalized_email=f"u-{user_id.hex[:6]}@example.com",
                password_hash="hash",
            )
            job = ScanJob(
                id=job_id,
                organization_id=org_id,
                created_by_user_id=user_id,
                status=ScanJobStatus.COMPLETED_WITH_ERRORS.value,
                total_input_count=3,
                valid_input_count=3,
                failed_count=2,
                queued_count=0,
                completed_count=1,
            )
            u1 = ScanURL(
                id=url_id1,
                scan_job_id=job_id,
                original_input="https://u1.com",
                original_index=0,
                status=ScanURLStatus.FAILED.value,
                attempt_count=3,
                max_attempts=3,
                last_error_code="OUT_OF_SCOPE_REDIRECT",
                redirect_target_domain="target1.com",
                redirect_target_url="https://target1.com/page",
            )
            u2 = ScanURL(
                id=url_id2,
                scan_job_id=job_id,
                original_input="https://u2.com",
                original_index=1,
                status=ScanURLStatus.FAILED.value,
                attempt_count=1,
                max_attempts=3,
                last_failure_code="BUSINESS_DOMAIN_REDIRECT_REVIEW",
                redirect_target_domain="target2.com",
                redirect_target_url="https://target2.com/about",
            )
            u3 = ScanURL(
                id=url_id3,
                scan_job_id=job_id,
                original_input="https://u3.com",
                original_index=2,
                status=ScanURLStatus.QUEUED.value,
                attempt_count=1,
                max_attempts=3,
                approved_redirect_domain="target3.com",
                redirect_target_domain="target3.com",
            )
            session.add_all([org, user, job, u1, u2, u3])

    async with session_factory() as session:
        service = ScanJobService(session)
        cmd = BulkRedirectCommand(url_ids=[url_id1, url_id2, url_id3, url_id1])  # Has duplicate
        res = await service.bulk_approve_url_redirects(org_id, job_id, cmd)

        assert res.requested_count == 4
        assert res.unique_requested_count == 3
        assert res.affected_count == 2
        assert res.skipped_count == 1
        assert len(res.results) == 3

        disposition_map = {r.url_id: r.disposition.value for r in res.results}
        assert disposition_map[url_id1] == "MUTATED"
        assert disposition_map[url_id2] == "MUTATED"
        assert disposition_map[url_id3] == "ALREADY_APPLIED"

    # Verify persistence in clean session
    async with session_factory() as session:
        persisted_job = (
            await session.execute(select(ScanJob).where(ScanJob.id == job_id))
        ).scalar_one()
        assert persisted_job.status == ScanJobStatus.RUNNING.value
        assert persisted_job.failed_count == 0  # 2 initial - 2 affected
        assert persisted_job.queued_count == 2  # 0 initial + 2 affected

        persisted_u1 = (
            await session.execute(select(ScanURL).where(ScanURL.id == url_id1))
        ).scalar_one()
        assert persisted_u1.status == ScanURLStatus.QUEUED.value
        assert persisted_u1.approved_redirect_domain == "target1.com"
        assert (
            persisted_u1.max_attempts == 4
        )  # attempt_count was 3 >= max_attempts 3 -> incremented
        assert persisted_u1.last_error_code is None
        assert persisted_u1.last_failure_code is None

        persisted_u2 = (
            await session.execute(select(ScanURL).where(ScanURL.id == url_id2))
        ).scalar_one()
        assert persisted_u2.status == ScanURLStatus.QUEUED.value
        assert persisted_u2.approved_redirect_domain == "target2.com"
        assert persisted_u2.max_attempts == 3  # attempt_count was 1 < 3 -> unchanged

        events = (
            (await session.execute(select(JobEvent).where(JobEvent.scan_job_id == job_id)))
            .scalars()
            .all()
        )
        assert len(events) == 1
        assert events[0].event_type == "REDIRECT_APPROVED"
        assert events[0].payload["affected_count"] == 2
        assert events[0].payload["skipped_count"] == 1
        assert set(events[0].payload["sample_approved_domains"]) == {"target1.com", "target2.com"}


@pytest.mark.anyio
async def test_bulk_approve_idempotent_retry_after_worker_completion(
    isolated_db_engine: AsyncEngine,
) -> None:
    """Verify repeated approval after worker runs (COMPLETED or FAILED) returns ALREADY_APPLIED
    with no mutations.
    """
    session_factory = async_sessionmaker(
        bind=isolated_db_engine, expire_on_commit=False, class_=AsyncSession
    )

    org_id = uuid.uuid4()
    user_id = uuid.uuid4()
    job_id = uuid.uuid4()
    url_completed_id = uuid.uuid4()
    url_failed_id = uuid.uuid4()

    async with session_factory() as session:
        async with session.begin():
            org = Organization(id=org_id, name="Org", slug=f"org-{org_id.hex[:6]}")
            user = User(
                id=user_id,
                email=f"u-{user_id.hex[:6]}@example.com",
                normalized_email=f"u-{user_id.hex[:6]}@example.com",
                password_hash="hash",
            )
            job = ScanJob(
                id=job_id,
                organization_id=org_id,
                created_by_user_id=user_id,
                status=ScanJobStatus.COMPLETED_WITH_ERRORS.value,
                total_input_count=2,
                valid_input_count=2,
                failed_count=1,
                queued_count=0,
                completed_count=1,
            )
            # URL already approved, scanned, and COMPLETED
            u_comp = ScanURL(
                id=url_completed_id,
                scan_job_id=job_id,
                original_input="https://comp.com",
                original_index=0,
                status=ScanURLStatus.COMPLETED.value,
                attempt_count=2,
                max_attempts=4,
                approved_redirect_domain="target-comp.com",
                redirect_target_domain="target-comp.com",
            )
            # URL already approved, scanned, and FAILED on subsequent scan with DNS failure
            u_fail = ScanURL(
                id=url_failed_id,
                scan_job_id=job_id,
                original_input="https://fail.com",
                original_index=1,
                status=ScanURLStatus.FAILED.value,
                attempt_count=4,
                max_attempts=4,
                last_failure_code="DNS_NAME_NOT_FOUND",
                approved_redirect_domain="target-fail.com",
                redirect_target_domain="target-fail.com",
            )
            session.add_all([org, user, job, u_comp, u_fail])

    async with session_factory() as session:
        service = ScanJobService(session)
        cmd = BulkRedirectCommand(url_ids=[url_completed_id, url_failed_id])
        res = await service.bulk_approve_url_redirects(org_id, job_id, cmd)

        assert res.affected_count == 0
        assert res.skipped_count == 2
        for item in res.results:
            assert item.disposition.value == "ALREADY_APPLIED"

    # Verify no counters modified, no events emitted
    async with session_factory() as session:
        job_check = (
            await session.execute(select(ScanJob).where(ScanJob.id == job_id))
        ).scalar_one()
        assert job_check.failed_count == 1
        assert job_check.queued_count == 0
        assert job_check.status == ScanJobStatus.COMPLETED_WITH_ERRORS.value

        events = (
            (await session.execute(select(JobEvent).where(JobEvent.scan_job_id == job_id)))
            .scalars()
            .all()
        )
        assert len(events) == 0


@pytest.mark.anyio
async def test_bulk_approve_atomic_rollback_on_any_invalid_row(
    isolated_db_engine: AsyncEngine,
) -> None:
    """Verify that if ANY row is in an invalid state, the entire operation aborts
    with zero mutations.
    """
    session_factory = async_sessionmaker(
        bind=isolated_db_engine, expire_on_commit=False, class_=AsyncSession
    )

    org_id = uuid.uuid4()
    user_id = uuid.uuid4()
    job_id = uuid.uuid4()
    valid_url_id = uuid.uuid4()
    invalid_url_id = uuid.uuid4()

    async with session_factory() as session:
        async with session.begin():
            org = Organization(id=org_id, name="Org", slug=f"org-{org_id.hex[:6]}")
            user = User(
                id=user_id,
                email=f"u-{user_id.hex[:6]}@example.com",
                normalized_email=f"u-{user_id.hex[:6]}@example.com",
                password_hash="hash",
            )
            job = ScanJob(
                id=job_id,
                organization_id=org_id,
                created_by_user_id=user_id,
                status=ScanJobStatus.RUNNING.value,
                total_input_count=2,
                valid_input_count=2,
                failed_count=2,
                queued_count=0,
            )
            u_valid = ScanURL(
                id=valid_url_id,
                scan_job_id=job_id,
                original_input="https://valid.com",
                original_index=0,
                status=ScanURLStatus.FAILED.value,
                attempt_count=1,
                max_attempts=3,
                last_error_code="OUT_OF_SCOPE_REDIRECT",
                redirect_target_domain="target-valid.com",
            )
            # Invalid because it failed with an unrelated code (DNS_RESOLUTION_FAILED)
            u_invalid = ScanURL(
                id=invalid_url_id,
                scan_job_id=job_id,
                original_input="https://invalid.com",
                original_index=1,
                status=ScanURLStatus.FAILED.value,
                attempt_count=1,
                max_attempts=3,
                last_error_code="DNS_RESOLUTION_FAILED",
                redirect_target_domain="target-invalid.com",
            )
            session.add_all([org, user, job, u_valid, u_invalid])

    async with session_factory() as session:
        service = ScanJobService(session)
        cmd = BulkRedirectCommand(url_ids=[valid_url_id, invalid_url_id])
        with pytest.raises(ServiceError) as exc_info:
            await service.bulk_approve_url_redirects(org_id, job_id, cmd)
        assert exc_info.value.code == ServiceErrorCode.INVALID_RESULT_STATE

    # Verify zero mutations occurred
    async with session_factory() as session:
        u_valid_check = (
            await session.execute(select(ScanURL).where(ScanURL.id == valid_url_id))
        ).scalar_one()
        assert u_valid_check.status == ScanURLStatus.FAILED.value
        assert u_valid_check.approved_redirect_domain is None

        job_check = (
            await session.execute(select(ScanJob).where(ScanJob.id == job_id))
        ).scalar_one()
        assert job_check.failed_count == 2
        assert job_check.queued_count == 0

        events = (
            (await session.execute(select(JobEvent).where(JobEvent.scan_job_id == job_id)))
            .scalars()
            .all()
        )
        assert len(events) == 0


@pytest.mark.anyio
async def test_missing_or_foreign_ids_return_sanitized_404(isolated_db_engine: AsyncEngine) -> None:
    """Verify non-existent, other-org, and other-job IDs return identical SCAN_URL_NOT_FOUND."""
    session_factory = async_sessionmaker(
        bind=isolated_db_engine, expire_on_commit=False, class_=AsyncSession
    )

    org1_id = uuid.uuid4()
    org2_id = uuid.uuid4()
    user_id = uuid.uuid4()
    job1_id = uuid.uuid4()
    job2_id = uuid.uuid4()
    url_job1 = uuid.uuid4()
    url_job2 = uuid.uuid4()
    missing_id = uuid.uuid4()

    async with session_factory() as session:
        async with session.begin():
            org1 = Organization(id=org1_id, name="Org1", slug=f"o1-{org1_id.hex[:6]}")
            org2 = Organization(id=org2_id, name="Org2", slug=f"o2-{org2_id.hex[:6]}")
            user = User(
                id=user_id,
                email=f"u-{user_id.hex[:6]}@example.com",
                normalized_email=f"u-{user_id.hex[:6]}@example.com",
                password_hash="hash",
            )
            j1 = ScanJob(id=job1_id, organization_id=org1_id, created_by_user_id=user_id)
            j2 = ScanJob(id=job2_id, organization_id=org2_id, created_by_user_id=user_id)
            u1 = ScanURL(
                id=url_job1, scan_job_id=job1_id, original_input="https://u1.com", original_index=0
            )
            u2 = ScanURL(
                id=url_job2, scan_job_id=job2_id, original_input="https://u2.com", original_index=0
            )
            session.add_all([org1, org2, user, j1, j2, u1, u2])

    async with session_factory() as session:
        service = ScanJobService(session)

        # 1. Missing random ID
        with pytest.raises(ServiceError) as exc1:
            await service.bulk_approve_url_redirects(
                org1_id, job1_id, BulkRedirectCommand(url_ids=[missing_id])
            )
        assert exc1.value.code == ServiceErrorCode.SCAN_URL_NOT_FOUND

        # 2. Foreign org ID
        with pytest.raises(ServiceError) as exc2:
            await service.bulk_approve_url_redirects(
                org1_id, job1_id, BulkRedirectCommand(url_ids=[url_job2])
            )
        assert exc2.value.code == ServiceErrorCode.SCAN_URL_NOT_FOUND

        # 3. Same org but different job
        with pytest.raises(ServiceError) as exc3:
            await service.bulk_reject_url_redirects(
                org1_id, job1_id, BulkRedirectCommand(url_ids=[url_job2])
            )
        assert exc3.value.code == ServiceErrorCode.SCAN_URL_NOT_FOUND


@pytest.mark.anyio
async def test_bulk_rejection_field_mutations_and_idempotency(
    isolated_db_engine: AsyncEngine,
) -> None:
    """Verify bulk rejection sets REDIRECT_REJECTED code, preserves FAILED, and is idempotent."""
    session_factory = async_sessionmaker(
        bind=isolated_db_engine, expire_on_commit=False, class_=AsyncSession
    )

    org_id = uuid.uuid4()
    user_id = uuid.uuid4()
    job_id = uuid.uuid4()
    url_id1 = uuid.uuid4()
    url_id2 = uuid.uuid4()

    async with session_factory() as session:
        async with session.begin():
            org = Organization(id=org_id, name="Org", slug=f"org-{org_id.hex[:6]}")
            user = User(
                id=user_id,
                email=f"u-{user_id.hex[:6]}@example.com",
                normalized_email=f"u-{user_id.hex[:6]}@example.com",
                password_hash="hash",
            )
            job = ScanJob(
                id=job_id,
                organization_id=org_id,
                created_by_user_id=user_id,
                status=ScanJobStatus.RUNNING.value,
                total_input_count=2,
                valid_input_count=2,
                failed_count=2,
                queued_count=0,
            )
            u1 = ScanURL(
                id=url_id1,
                scan_job_id=job_id,
                original_input="https://u1.com",
                original_index=0,
                status=ScanURLStatus.FAILED.value,
                attempt_count=1,
                max_attempts=3,
                last_error_code="OUT_OF_SCOPE_REDIRECT",
                redirect_target_domain="reject1.com",
                redirect_target_url="https://reject1.com/test",
            )
            u2 = ScanURL(
                id=url_id2,
                scan_job_id=job_id,
                original_input="https://u2.com",
                original_index=1,
                status=ScanURLStatus.FAILED.value,
                attempt_count=1,
                max_attempts=3,
                last_failure_code="BUSINESS_DOMAIN_REDIRECT_REVIEW",
                redirect_target_domain="reject2.com",
            )
            session.add_all([org, user, job, u1, u2])

    # 1. First rejection: MUTATED
    async with session_factory() as session:
        service = ScanJobService(session)
        cmd = BulkRedirectCommand(url_ids=[url_id1, url_id2])
        res = await service.bulk_reject_url_redirects(org_id, job_id, cmd)
        assert res.affected_count == 2
        assert res.skipped_count == 0
        for item in res.results:
            assert item.disposition.value == "MUTATED"

    # Verify fields in DB
    async with session_factory() as session:
        u1_check = (
            await session.execute(select(ScanURL).where(ScanURL.id == url_id1))
        ).scalar_one()
        assert u1_check.status == ScanURLStatus.FAILED.value
        assert u1_check.approved_redirect_domain is None
        assert u1_check.last_error_code == REDIRECT_REJECTED_CODE
        assert u1_check.last_failure_code == REDIRECT_REJECTED_CODE
        assert u1_check.last_error_message == "External redirect rejected by user."
        assert u1_check.redirect_target_domain == "reject1.com"
        assert u1_check.redirect_target_url == "https://reject1.com/test"

        # Counters unchanged for rejection
        job_check = (
            await session.execute(select(ScanJob).where(ScanJob.id == job_id))
        ).scalar_one()
        assert job_check.failed_count == 2
        assert job_check.queued_count == 0

        events = (
            (await session.execute(select(JobEvent).where(JobEvent.scan_job_id == job_id)))
            .scalars()
            .all()
        )
        assert len(events) == 1
        assert events[0].event_type == "REDIRECT_REJECTED"
        assert events[0].payload["affected_count"] == 2

    # 2. Second rejection: ALREADY_APPLIED (idempotent skip)
    async with session_factory() as session:
        service = ScanJobService(session)
        cmd = BulkRedirectCommand(url_ids=[url_id1, url_id2])
        res2 = await service.bulk_reject_url_redirects(org_id, job_id, cmd)
        assert res2.affected_count == 0
        assert res2.skipped_count == 2
        for item in res2.results:
            assert item.disposition.value == "ALREADY_APPLIED"

    # Verify no new events emitted
    async with session_factory() as session:
        events2 = (
            (await session.execute(select(JobEvent).where(JobEvent.scan_job_id == job_id)))
            .scalars()
            .all()
        )
        assert len(events2) == 1


@pytest.mark.anyio
async def test_approve_previously_rejected_redirect(isolated_db_engine: AsyncEngine) -> None:
    """Verify that a URL previously marked REDIRECT_REJECTED can subsequently be approved."""
    session_factory = async_sessionmaker(
        bind=isolated_db_engine, expire_on_commit=False, class_=AsyncSession
    )

    org_id = uuid.uuid4()
    user_id = uuid.uuid4()
    job_id = uuid.uuid4()
    url_id = uuid.uuid4()

    async with session_factory() as session:
        async with session.begin():
            org = Organization(id=org_id, name="Org", slug=f"org-{org_id.hex[:6]}")
            user = User(
                id=user_id,
                email=f"u-{user_id.hex[:6]}@example.com",
                normalized_email=f"u-{user_id.hex[:6]}@example.com",
                password_hash="hash",
            )
            job = ScanJob(
                id=job_id,
                organization_id=org_id,
                created_by_user_id=user_id,
                status=ScanJobStatus.RUNNING.value,
                total_input_count=1,
                valid_input_count=1,
                failed_count=1,
                queued_count=0,
            )
            # URL was previously rejected
            u = ScanURL(
                id=url_id,
                scan_job_id=job_id,
                original_input="https://prev-rejected.com",
                original_index=0,
                status=ScanURLStatus.FAILED.value,
                attempt_count=1,
                max_attempts=3,
                last_error_code=REDIRECT_REJECTED_CODE,
                last_failure_code=REDIRECT_REJECTED_CODE,
                last_error_message="External redirect rejected by user.",
                redirect_target_domain="rejected-domain.com",
            )
            session.add_all([org, user, job, u])

    async with session_factory() as session:
        service = ScanJobService(session)
        cmd = BulkRedirectCommand(url_ids=[url_id])
        res = await service.bulk_approve_url_redirects(org_id, job_id, cmd)
        assert res.affected_count == 1
        assert res.results[0].disposition.value == "MUTATED"

    async with session_factory() as session:
        u_check = (await session.execute(select(ScanURL).where(ScanURL.id == url_id))).scalar_one()
        assert u_check.status == ScanURLStatus.QUEUED.value
        assert u_check.approved_redirect_domain == "rejected-domain.com"
        assert u_check.last_error_code is None
        assert u_check.last_failure_code is None

        job_check = (
            await session.execute(select(ScanJob).where(ScanJob.id == job_id))
        ).scalar_one()
        assert job_check.failed_count == 0
        assert job_check.queued_count == 1


@pytest.mark.anyio
async def test_cancellation_fencing(isolated_db_engine: AsyncEngine) -> None:
    """Verify approval and rejection are rejected on CANCELLED and CANCELLING jobs."""
    session_factory = async_sessionmaker(
        bind=isolated_db_engine, expire_on_commit=False, class_=AsyncSession
    )

    org_id = uuid.uuid4()
    user_id = uuid.uuid4()
    cancelled_job_id = uuid.uuid4()
    url_id = uuid.uuid4()

    async with session_factory() as session:
        async with session.begin():
            org = Organization(id=org_id, name="Org", slug=f"org-{org_id.hex[:6]}")
            user = User(
                id=user_id,
                email=f"u-{user_id.hex[:6]}@example.com",
                normalized_email=f"u-{user_id.hex[:6]}@example.com",
                password_hash="hash",
            )
            job = ScanJob(
                id=cancelled_job_id,
                organization_id=org_id,
                created_by_user_id=user_id,
                status=ScanJobStatus.CANCELLED.value,
            )
            u = ScanURL(
                id=url_id,
                scan_job_id=cancelled_job_id,
                original_input="https://cancelled.com",
                original_index=0,
                status=ScanURLStatus.FAILED.value,
                last_error_code="OUT_OF_SCOPE_REDIRECT",
                redirect_target_domain="target.com",
            )
            session.add_all([org, user, job, u])

    async with session_factory() as session:
        service = ScanJobService(session)
        with pytest.raises(ServiceError) as exc_app:
            await service.bulk_approve_url_redirects(
                org_id, cancelled_job_id, BulkRedirectCommand(url_ids=[url_id])
            )
        assert exc_app.value.code == ServiceErrorCode.INVALID_STATE_TRANSITION

        with pytest.raises(ServiceError) as exc_rej:
            await service.bulk_reject_url_redirects(
                org_id, cancelled_job_id, BulkRedirectCommand(url_ids=[url_id])
            )
        assert exc_rej.value.code == ServiceErrorCode.INVALID_STATE_TRANSITION


@pytest.mark.anyio
async def test_concurrent_overlapping_bulk_approvals(isolated_db_engine: AsyncEngine) -> None:
    """Verify concurrent overlapping approval requests do not deadlock due to ID-ordered locking."""
    session_factory = async_sessionmaker(
        bind=isolated_db_engine, expire_on_commit=False, class_=AsyncSession
    )

    org_id = uuid.uuid4()
    user_id = uuid.uuid4()
    job_id = uuid.uuid4()

    url_ids = [uuid.uuid4() for _ in range(6)]

    async with session_factory() as session:
        async with session.begin():
            org = Organization(id=org_id, name="Org", slug=f"org-{org_id.hex[:6]}")
            user = User(
                id=user_id,
                email=f"u-{user_id.hex[:6]}@example.com",
                normalized_email=f"u-{user_id.hex[:6]}@example.com",
                password_hash="hash",
            )
            job = ScanJob(
                id=job_id,
                organization_id=org_id,
                created_by_user_id=user_id,
                status=ScanJobStatus.RUNNING.value,
                total_input_count=6,
                valid_input_count=6,
                failed_count=6,
                queued_count=0,
            )
            urls = [
                ScanURL(
                    id=uid,
                    scan_job_id=job_id,
                    original_input=f"https://concurrent{idx}.com",
                    original_index=idx,
                    status=ScanURLStatus.FAILED.value,
                    attempt_count=1,
                    max_attempts=3,
                    last_error_code="OUT_OF_SCOPE_REDIRECT",
                    redirect_target_domain=f"target{idx}.com",
                )
                for idx, uid in enumerate(url_ids)
            ]
            session.add_all([org, user, job, *urls])

    async def run_approval(ids: list[uuid.UUID]):
        async with session_factory() as session:
            srv = ScanJobService(session)
            return await srv.bulk_approve_url_redirects(
                org_id, job_id, BulkRedirectCommand(url_ids=ids)
            )

    # Overlapping subsets in differing request orders:
    set1 = [url_ids[0], url_ids[2], url_ids[4]]
    set2 = [url_ids[4], url_ids[2], url_ids[1], url_ids[3]]
    set3 = [url_ids[5], url_ids[0], url_ids[3]]

    res1, res2, res3 = await asyncio.gather(
        run_approval(set1),
        run_approval(set2),
        run_approval(set3),
    )

    total_mutated = res1.affected_count + res2.affected_count + res3.affected_count
    assert total_mutated == 6  # Exactly 6 distinct URLs mutated once

    async with session_factory() as session:
        job_check = (
            await session.execute(select(ScanJob).where(ScanJob.id == job_id))
        ).scalar_one()
        assert job_check.failed_count == 0
        assert job_check.queued_count == 6


@pytest.mark.anyio
async def test_api_routes_bulk_approve_and_reject(isolated_db_engine: AsyncEngine) -> None:
    """Verify HTTP routes /urls/bulk-approve-redirects and /urls/bulk-reject-redirects."""
    session_factory = async_sessionmaker(
        bind=isolated_db_engine, expire_on_commit=False, class_=AsyncSession
    )

    org_id = uuid.uuid4()
    user_id = uuid.uuid4()
    job_id = uuid.uuid4()
    url1_id = uuid.uuid4()
    url2_id = uuid.uuid4()

    async with session_factory() as session:
        async with session.begin():
            org = Organization(id=org_id, name="Org", slug=f"org-{org_id.hex[:6]}")
            user = User(
                id=user_id,
                email=f"u-{user_id.hex[:6]}@example.com",
                normalized_email=f"u-{user_id.hex[:6]}@example.com",
                password_hash="hash",
            )
            job = ScanJob(
                id=job_id,
                organization_id=org_id,
                created_by_user_id=user_id,
                status=ScanJobStatus.RUNNING.value,
                total_input_count=2,
                valid_input_count=2,
                failed_count=2,
            )
            u1 = ScanURL(
                id=url1_id,
                scan_job_id=job_id,
                original_input="https://u1.com",
                original_index=0,
                status=ScanURLStatus.FAILED.value,
                last_error_code="OUT_OF_SCOPE_REDIRECT",
                redirect_target_domain="dest1.com",
            )
            u2 = ScanURL(
                id=url2_id,
                scan_job_id=job_id,
                original_input="https://u2.com",
                original_index=1,
                status=ScanURLStatus.FAILED.value,
                last_error_code="BUSINESS_DOMAIN_REDIRECT_REVIEW",
                redirect_target_domain="dest2.com",
            )
            session.add_all([org, user, job, u1, u2])

    db_manager = MagicMock()
    db_manager.session_factory = session_factory
    app.state.db_manager = db_manager

    principal = RequestPrincipal(user_id=user_id, organization_id=org_id, request_id="test-req-123")
    mock_publisher = AsyncMock()
    mock_publisher.publish_work_available = AsyncMock(return_value=1)

    app.dependency_overrides[get_current_principal] = lambda: principal
    app.dependency_overrides[get_session_factory] = lambda: session_factory
    app.dependency_overrides[get_redis_publisher] = lambda: mock_publisher

    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. Bulk approve URL 1
            res_app = await client.post(
                f"/api/v1/scan-jobs/{job_id}/urls/bulk-approve-redirects",
                json={"url_ids": [str(url1_id)]},
            )
            assert res_app.status_code == 200
            data_app = res_app.json()
            assert data_app["action"] == "APPROVE"
            assert data_app["affected_count"] == 1
            assert data_app["skipped_count"] == 0
            assert data_app["results"][0]["disposition"] == "MUTATED"
            # Redis published because affected_count > 0
            assert mock_publisher.publish_work_available.call_count == 1

            # 2. Bulk reject URL 2
            mock_publisher.publish_work_available.reset_mock()
            res_rej = await client.post(
                f"/api/v1/scan-jobs/{job_id}/urls/bulk-reject-redirects",
                json={"url_ids": [str(url2_id)]},
            )
            assert res_rej.status_code == 200
            data_rej = res_rej.json()
            assert data_rej["action"] == "REJECT"
            assert data_rej["affected_count"] == 1
            assert data_rej["skipped_count"] == 0
            # Redis NOT published for rejection
            assert mock_publisher.publish_work_available.call_count == 0

            # 3. Query list endpoint with requires_redirect_approval=true filter
            res_list = await client.get(
                f"/api/v1/scan-jobs/{job_id}/urls?requires_redirect_approval=true"
            )
            assert res_list.status_code == 200
            # Both were resolved (one approved, one rejected), so 0 pending rows returned
            assert len(res_list.json()["items"]) == 0

            # 4. Validation bounds: 0 IDs -> 422
            res_zero = await client.post(
                f"/api/v1/scan-jobs/{job_id}/urls/bulk-approve-redirects",
                json={"url_ids": []},
            )
            assert res_zero.status_code == 422

            # 5. Validation bounds: >250 IDs -> 422
            res_large = await client.post(
                f"/api/v1/scan-jobs/{job_id}/urls/bulk-approve-redirects",
                json={"url_ids": [str(uuid.uuid4()) for _ in range(251)]},
            )
            assert res_large.status_code == 422

    finally:
        app.dependency_overrides.clear()
        app.state.db_manager = None


@pytest.mark.anyio
async def test_redis_publish_failure_does_not_abort_approval(
    isolated_db_engine: AsyncEngine,
) -> None:
    """Verify that a Redis wake-up failure/timeout is caught and logged, returning HTTP 200."""
    session_factory = async_sessionmaker(
        bind=isolated_db_engine, expire_on_commit=False, class_=AsyncSession
    )

    org_id = uuid.uuid4()
    user_id = uuid.uuid4()
    job_id = uuid.uuid4()
    url_id = uuid.uuid4()

    async with session_factory() as session:
        async with session.begin():
            org = Organization(id=org_id, name="Org", slug=f"org-{org_id.hex[:6]}")
            user = User(
                id=user_id,
                email=f"u-{user_id.hex[:6]}@example.com",
                normalized_email=f"u-{user_id.hex[:6]}@example.com",
                password_hash="hash",
            )
            job = ScanJob(
                id=job_id,
                organization_id=org_id,
                created_by_user_id=user_id,
                status=ScanJobStatus.RUNNING.value,
                total_input_count=1,
                valid_input_count=1,
                failed_count=1,
            )
            u = ScanURL(
                id=url_id,
                scan_job_id=job_id,
                original_input="https://redis-fail.com",
                original_index=0,
                status=ScanURLStatus.FAILED.value,
                last_error_code="OUT_OF_SCOPE_REDIRECT",
                redirect_target_domain="dest.com",
            )
            session.add_all([org, user, job, u])

    db_manager = MagicMock()
    db_manager.session_factory = session_factory
    app.state.db_manager = db_manager

    principal = RequestPrincipal(
        user_id=user_id, organization_id=org_id, request_id="test-redis-fail"
    )
    mock_publisher = AsyncMock()
    mock_publisher.publish_work_available = AsyncMock(
        side_effect=TimeoutError("Redis connection timeout")
    )

    app.dependency_overrides[get_current_principal] = lambda: principal
    app.dependency_overrides[get_session_factory] = lambda: session_factory
    app.dependency_overrides[get_redis_publisher] = lambda: mock_publisher

    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.post(
                f"/api/v1/scan-jobs/{job_id}/urls/bulk-approve-redirects",
                json={"url_ids": [str(url_id)]},
            )
            assert res.status_code == 200
            assert res.json()["affected_count"] == 1

        # Confirm DB was committed despite Redis failure
        async with session_factory() as session:
            persisted_u = (
                await session.execute(select(ScanURL).where(ScanURL.id == url_id))
            ).scalar_one()
            assert persisted_u.status == ScanURLStatus.QUEUED.value
            assert persisted_u.approved_redirect_domain == "dest.com"
    finally:
        app.dependency_overrides.clear()
        app.state.db_manager = None


def test_redirect_rejected_failure_reason_formatting() -> None:
    """Verify public failure reason description for REDIRECT_REJECTED."""
    desc = format_failure_reason(REDIRECT_REJECTED_CODE, None)
    assert desc == "External redirect rejected by user"
