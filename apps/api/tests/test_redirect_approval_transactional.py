"""Transactional tests for redirect approval correctness, lock order, and idempotency."""

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
from email_discovery_api.models import JobEvent, Organization, ScanJob, ScanURL, User
from email_discovery_api.models.enums import ScanJobStatus, ScanURLStatus
from email_discovery_api.services.crawl_work import CrawlWorkService
from email_discovery_api.services.errors import ServiceError, ServiceErrorCode
from email_discovery_api.services.scan_jobs import ScanJobService

_SENTINEL = object()


@pytest.mark.anyio
async def test_redirect_approval_persists_in_independent_session(
    isolated_db_engine: AsyncEngine,
) -> None:
    """Verify redirect approval commits mutations and events visible to new session."""
    session_factory = async_sessionmaker(
        bind=isolated_db_engine, expire_on_commit=False, class_=AsyncSession
    )

    org_id = uuid.uuid4()
    user_id = uuid.uuid4()
    job_id = uuid.uuid4()
    url_id = uuid.uuid4()

    # 1. Seed initial database state in a short transaction
    async with session_factory() as session:
        async with session.begin():
            org = Organization(id=org_id, name="Approval Org", slug=f"org-{org_id.hex[:6]}")
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
                status=ScanJobStatus.COMPLETED_WITH_ERRORS.value,
                total_input_count=2,
                valid_input_count=2,
                completed_count=1,
                failed_count=1,
                queued_count=0,
            )
            url = ScanURL(
                id=url_id,
                scan_job_id=job_id,
                original_input="https://old-target.com",
                normalized_domain="old-target.com",
                original_index=0,
                status=ScanURLStatus.FAILED.value,
                attempt_count=3,
                max_attempts=3,
                last_error_code="OUT_OF_SCOPE_REDIRECT",
                last_error_message="Redirected to new-target.com",
                approved_redirect_domain=None,
                redirect_target_domain="new-target.com",
                redirect_target_url="https://new-target.com/page",
            )
            session.add_all([org, user, job, url])

    # 2. Execute approve_url_redirect in a separate service session that closes
    async with session_factory() as session:
        service = ScanJobService(session)

        approved_url = await service.approve_url_redirect(
            organization_id=org_id,
            job_id=job_id,
            url_id=url_id,
            approved_target_domain="new-target.com",
        )
        assert approved_url.status == ScanURLStatus.QUEUED.value

    # 3. Open a BRAND-NEW independent database session and verify committed persistence
    async with session_factory() as independent_session:
        # Check ScanURL row
        res_url = await independent_session.execute(select(ScanURL).where(ScanURL.id == url_id))
        persisted_url = res_url.scalar_one()
        assert persisted_url.approved_redirect_domain == "new-target.com"
        assert persisted_url.status == ScanURLStatus.QUEUED.value
        assert persisted_url.max_attempts == 4  # Increased from 3 because attempt_count == 3
        assert persisted_url.completed_at is None
        assert persisted_url.lease_owner is None
        assert persisted_url.lease_expires_at is None
        assert persisted_url.last_error_code is None
        assert persisted_url.last_error_message is None

        # Check ScanJob row
        res_job = await independent_session.execute(select(ScanJob).where(ScanJob.id == job_id))
        persisted_job = res_job.scalar_one()
        assert persisted_job.status == ScanJobStatus.RUNNING.value
        assert persisted_job.failed_count == 0
        assert persisted_job.queued_count == 1
        assert persisted_job.completed_at is None

        # Check JobEvent row
        res_event = await independent_session.execute(
            select(JobEvent).where(JobEvent.scan_job_id == job_id)
        )
        events = res_event.scalars().all()
        assert len(events) == 1
        assert events[0].event_type == "REDIRECT_APPROVED"
        assert events[0].payload["approved_redirect_domain"] == "new-target.com"


@pytest.mark.anyio
async def test_approved_url_is_claimable_by_worker(
    isolated_db_engine: AsyncEngine,
) -> None:
    """Verify that an approved redirect URL is successfully claimed by CrawlWorkService."""
    session_factory = async_sessionmaker(
        bind=isolated_db_engine, expire_on_commit=False, class_=AsyncSession
    )

    org_id = uuid.uuid4()
    user_id = uuid.uuid4()
    job_id = uuid.uuid4()
    url_id = uuid.uuid4()

    async with session_factory() as session:
        async with session.begin():
            org = Organization(id=org_id, name="Claim Org", slug=f"org-{org_id.hex[:6]}")
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
                status=ScanJobStatus.FAILED.value,
                total_input_count=1,
                valid_input_count=1,
                completed_count=0,
                failed_count=1,
                queued_count=0,
            )
            url = ScanURL(
                id=url_id,
                scan_job_id=job_id,
                original_input="https://redirecting-site.com",
                normalized_domain="redirecting-site.com",
                original_index=0,
                status=ScanURLStatus.FAILED.value,
                attempt_count=1,
                max_attempts=3,
                last_error_code="OUT_OF_SCOPE_REDIRECT",
                approved_redirect_domain=None,
                redirect_target_domain="approved-dest.com",
                redirect_target_url="https://approved-dest.com/main",
            )
            session.add_all([org, user, job, url])

    # Approve redirect
    async with session_factory() as session:
        service = ScanJobService(session)
        await service.approve_url_redirect(org_id, job_id, url_id)

    # Worker attempts claim
    async with session_factory() as session:
        crawl_service = CrawlWorkService(session)
        claim = await crawl_service.claim_next_url(lease_owner="worker-unit-test")

        assert claim is not None
        assert claim.scan_url_id == url_id
        assert claim.lease_owner == "worker-unit-test"

    # Verify claim status in DB
    async with session_factory() as session:
        res = await session.execute(select(ScanURL).where(ScanURL.id == url_id))
        claimed_url = res.scalar_one()
        assert claimed_url.status == ScanURLStatus.SCANNING.value
        assert claimed_url.lease_owner == "worker-unit-test"
        assert claimed_url.approved_redirect_domain == "approved-dest.com"


@pytest.mark.anyio
async def test_redirect_approval_idempotency(
    isolated_db_engine: AsyncEngine,
) -> None:
    """Verify repeating redirect approval is idempotent and causes zero duplicate mutations."""
    session_factory = async_sessionmaker(
        bind=isolated_db_engine, expire_on_commit=False, class_=AsyncSession
    )

    org_id = uuid.uuid4()
    user_id = uuid.uuid4()
    job_id = uuid.uuid4()
    url_id = uuid.uuid4()

    async with session_factory() as session:
        async with session.begin():
            org = Organization(id=org_id, name="Idempotent Org", slug=f"org-{org_id.hex[:6]}")
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
                status=ScanJobStatus.FAILED.value,
                total_input_count=1,
                valid_input_count=1,
                completed_count=0,
                failed_count=1,
                queued_count=0,
            )
            url = ScanURL(
                id=url_id,
                scan_job_id=job_id,
                original_input="https://site.com",
                normalized_domain="site.com",
                original_index=0,
                status=ScanURLStatus.FAILED.value,
                attempt_count=1,
                max_attempts=3,
                last_error_code="OUT_OF_SCOPE_REDIRECT",
                approved_redirect_domain=None,
                redirect_target_domain="target.com",
                redirect_target_url="https://target.com/page",
            )
            session.add_all([org, user, job, url])

    # First approval
    async with session_factory() as session:
        service = ScanJobService(session)
        url1 = await service.approve_url_redirect(org_id, job_id, url_id)
        assert url1.status == ScanURLStatus.QUEUED.value

    # Second approval (repeat call)
    async with session_factory() as session:
        service = ScanJobService(session)
        url2 = await service.approve_url_redirect(org_id, job_id, url_id)
        assert url2.status == ScanURLStatus.QUEUED.value

    # Verify counts and events in independent session
    async with session_factory() as session:
        res_job = await session.execute(select(ScanJob).where(ScanJob.id == job_id))
        job_obj = res_job.scalar_one()
        assert job_obj.failed_count == 0
        assert job_obj.queued_count == 1

        res_events = await session.execute(select(JobEvent).where(JobEvent.scan_job_id == job_id))
        events = res_events.scalars().all()
        assert len(events) == 1  # Exactly 1 event, no duplicate event


@pytest.mark.anyio
async def test_redirect_approval_validations_and_rejections(
    isolated_db_engine: AsyncEngine,
) -> None:
    """Verify mismatched domain, cancelled job, and cross-org access raise ServiceError."""
    session_factory = async_sessionmaker(
        bind=isolated_db_engine, expire_on_commit=False, class_=AsyncSession
    )

    org_id = uuid.uuid4()
    other_org_id = uuid.uuid4()
    user_id = uuid.uuid4()
    job_id = uuid.uuid4()
    cancelled_job_id = uuid.uuid4()
    url_id = uuid.uuid4()
    cancelled_url_id = uuid.uuid4()

    async with session_factory() as session:
        async with session.begin():
            org = Organization(id=org_id, name="Rejection Org", slug=f"org-{org_id.hex[:6]}")
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
                status=ScanJobStatus.FAILED.value,
                total_input_count=1,
                valid_input_count=1,
                failed_count=1,
            )
            cancelled_job = ScanJob(
                id=cancelled_job_id,
                organization_id=org_id,
                created_by_user_id=user_id,
                status=ScanJobStatus.CANCELLED.value,
                total_input_count=1,
                valid_input_count=1,
                failed_count=1,
            )
            url = ScanURL(
                id=url_id,
                scan_job_id=job_id,
                original_input="https://site.com",
                normalized_domain="site.com",
                original_index=0,
                status=ScanURLStatus.FAILED.value,
                last_error_code="OUT_OF_SCOPE_REDIRECT",
                redirect_target_domain="expected-target.com",
            )
            cancelled_url = ScanURL(
                id=cancelled_url_id,
                scan_job_id=cancelled_job_id,
                original_input="https://canc.com",
                normalized_domain="canc.com",
                original_index=0,
                status=ScanURLStatus.FAILED.value,
                last_error_code="OUT_OF_SCOPE_REDIRECT",
                redirect_target_domain="some-target.com",
            )
            session.add_all([org, user, job, cancelled_job, url, cancelled_url])

    # 1. Target domain mismatch -> INVALID_RESULT_STATE
    async with session_factory() as session:
        service = ScanJobService(session)
        with pytest.raises(ServiceError) as exc_info:
            await service.approve_url_redirect(
                org_id, job_id, url_id, approved_target_domain="wrong-target.com"
            )
        assert exc_info.value.code == ServiceErrorCode.INVALID_RESULT_STATE

    # 2. Cancelled job rejection -> INVALID_STATE_TRANSITION
    async with session_factory() as session:
        service = ScanJobService(session)
        with pytest.raises(ServiceError) as exc_info:
            await service.approve_url_redirect(org_id, cancelled_job_id, cancelled_url_id)
        assert exc_info.value.code == ServiceErrorCode.INVALID_STATE_TRANSITION

    # 3. Cross-organization approval -> JOB_NOT_FOUND
    async with session_factory() as session:
        service = ScanJobService(session)
        with pytest.raises(ServiceError) as exc_info:
            await service.approve_url_redirect(other_org_id, job_id, url_id)
        assert exc_info.value.code == ServiceErrorCode.JOB_NOT_FOUND


@pytest.mark.anyio
async def test_forced_exception_rolls_back_approval_transaction(
    isolated_db_engine: AsyncEngine,
) -> None:
    """Verify an exception raised inside the approval transaction rolls back all mutations."""
    session_factory = async_sessionmaker(
        bind=isolated_db_engine, expire_on_commit=False, class_=AsyncSession
    )

    org_id = uuid.uuid4()
    user_id = uuid.uuid4()
    job_id = uuid.uuid4()
    url_id = uuid.uuid4()

    async with session_factory() as session:
        async with session.begin():
            org = Organization(id=org_id, name="Rollback Org", slug=f"org-{org_id.hex[:6]}")
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
                status=ScanJobStatus.FAILED.value,
                total_input_count=1,
                valid_input_count=1,
                failed_count=1,
                queued_count=0,
            )
            url = ScanURL(
                id=url_id,
                scan_job_id=job_id,
                original_input="https://rollback-test.com",
                normalized_domain="rollback-test.com",
                original_index=0,
                status=ScanURLStatus.FAILED.value,
                last_error_code="OUT_OF_SCOPE_REDIRECT",
                redirect_target_domain="rollback-target.com",
            )
            session.add_all([org, user, job, url])

    async with session_factory() as session:
        service = ScanJobService(session)
        service.job_repo.allocate_event_sequence = AsyncMock(
            side_effect=RuntimeError("Simulated DB error during sequence allocation")
        )
        with pytest.raises(RuntimeError, match="Simulated DB error"):
            await service.approve_url_redirect(org_id, job_id, url_id)

    # Verify complete rollback in independent session
    async with session_factory() as session:
        res_url = await session.execute(select(ScanURL).where(ScanURL.id == url_id))
        unmodified_url = res_url.scalar_one()
        assert unmodified_url.status == ScanURLStatus.FAILED.value
        assert unmodified_url.approved_redirect_domain is None

        res_job = await session.execute(select(ScanJob).where(ScanJob.id == job_id))
        unmodified_job = res_job.scalar_one()
        assert unmodified_job.status == ScanJobStatus.FAILED.value
        assert unmodified_job.failed_count == 1
        assert unmodified_job.queued_count == 0

        res_events = await session.execute(select(JobEvent).where(JobEvent.scan_job_id == job_id))
        assert len(res_events.scalars().all()) == 0


@pytest.mark.anyio
async def test_redis_publish_failure_leaves_db_approval_committed(
    isolated_db_engine: AsyncEngine,
) -> None:
    """Verify endpoint returns HTTP 200 and preserves approval when Redis publishing fails."""
    session_factory = async_sessionmaker(
        bind=isolated_db_engine, expire_on_commit=False, class_=AsyncSession
    )

    org_id = uuid.uuid4()
    user_id = uuid.uuid4()
    job_id = uuid.uuid4()
    url_id = uuid.uuid4()

    async with session_factory() as session:
        async with session.begin():
            org = Organization(id=org_id, name="Redis Fail Org", slug=f"org-{org_id.hex[:6]}")
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
                status=ScanJobStatus.FAILED.value,
                total_input_count=1,
                valid_input_count=1,
                failed_count=1,
            )
            url = ScanURL(
                id=url_id,
                scan_job_id=job_id,
                original_input="https://redis-fail.com",
                normalized_domain="redis-fail.com",
                original_index=0,
                status=ScanURLStatus.FAILED.value,
                last_error_code="OUT_OF_SCOPE_REDIRECT",
                redirect_target_domain="redis-target.com",
            )
            session.add_all([org, user, job, url])

    principal = RequestPrincipal(
        user_id=user_id,
        organization_id=org_id,
        request_id="test-redis-fail-req",
    )

    # Mock failing Redis publisher
    mock_failing_publisher = MagicMock()
    mock_failing_publisher.publish_work_available = AsyncMock(
        side_effect=TimeoutError("Redis timeout simulated")
    )

    had_db_manager = hasattr(app.state, "db_manager")
    orig_db_manager = getattr(app.state, "db_manager", None)
    orig_principal: Any = app.dependency_overrides.get(get_current_principal, _SENTINEL)
    orig_session_factory: Any = app.dependency_overrides.get(get_session_factory, _SENTINEL)
    orig_publisher: Any = app.dependency_overrides.get(get_redis_publisher, _SENTINEL)

    db_manager = MagicMock()
    db_manager.session_factory = session_factory

    try:
        app.state.db_manager = db_manager
        app.dependency_overrides[get_current_principal] = lambda: principal
        app.dependency_overrides[get_session_factory] = lambda: session_factory
        app.dependency_overrides[get_redis_publisher] = lambda: mock_failing_publisher

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            response = await client.post(
                f"/api/v1/scan-jobs/{job_id}/urls/{url_id}/approve-redirect"
            )
            assert response.status_code == 200
            data = response.json()
            assert data["approved_redirect_domain"] == "redis-target.com"
            assert data["status"] == "QUEUED"
    finally:
        if had_db_manager:
            app.state.db_manager = orig_db_manager
        elif hasattr(app.state, "db_manager"):
            delattr(app.state, "db_manager")

        if orig_principal is not _SENTINEL:
            app.dependency_overrides[get_current_principal] = orig_principal
        else:
            app.dependency_overrides.pop(get_current_principal, None)

        if orig_session_factory is not _SENTINEL:
            app.dependency_overrides[get_session_factory] = orig_session_factory
        else:
            app.dependency_overrides.pop(get_session_factory, None)

        if orig_publisher is not _SENTINEL:
            app.dependency_overrides[get_redis_publisher] = orig_publisher
        else:
            app.dependency_overrides.pop(get_redis_publisher, None)

    # Verify DB approval remains committed despite Redis failure
    async with session_factory() as session:
        res_url = await session.execute(select(ScanURL).where(ScanURL.id == url_id))
        committed_url = res_url.scalar_one()
        assert committed_url.approved_redirect_domain == "redis-target.com"
        assert committed_url.status == ScanURLStatus.QUEUED.value


@pytest.mark.anyio
async def test_concurrent_redirect_approvals_prevent_double_counting(
    isolated_db_engine: AsyncEngine,
) -> None:
    """Verify concurrent approval requests do not double-increment counters or events."""
    session_factory = async_sessionmaker(
        bind=isolated_db_engine, expire_on_commit=False, class_=AsyncSession
    )

    org_id = uuid.uuid4()
    user_id = uuid.uuid4()
    job_id = uuid.uuid4()
    url_id = uuid.uuid4()

    async with session_factory() as session:
        async with session.begin():
            org = Organization(id=org_id, name="Concurrent Org", slug=f"org-{org_id.hex[:6]}")
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
                status=ScanJobStatus.FAILED.value,
                total_input_count=1,
                valid_input_count=1,
                completed_count=0,
                failed_count=1,
                queued_count=0,
            )
            url = ScanURL(
                id=url_id,
                scan_job_id=job_id,
                original_input="https://concurrent.com",
                normalized_domain="concurrent.com",
                original_index=0,
                status=ScanURLStatus.FAILED.value,
                attempt_count=1,
                max_attempts=3,
                last_error_code="OUT_OF_SCOPE_REDIRECT",
                redirect_target_domain="concurrent-target.com",
            )
            session.add_all([org, user, job, url])

    async def _approve_task():
        async with session_factory() as session:
            service = ScanJobService(session)
            return await service.approve_url_redirect(org_id, job_id, url_id)

    # Execute two simultaneous approval tasks with strict 5-second timeout
    results = await asyncio.wait_for(
        asyncio.gather(_approve_task(), _approve_task(), return_exceptions=True),
        timeout=5.0,
    )

    # Verify both calls returned without unhandled exceptions
    for res in results:
        assert not isinstance(res, Exception), f"Concurrent task failed with: {res}"

    # Verify single counter adjustment and single event in DB
    async with session_factory() as session:
        res_job = await session.execute(select(ScanJob).where(ScanJob.id == job_id))
        job_obj = res_job.scalar_one()
        assert job_obj.failed_count == 0
        assert job_obj.queued_count == 1
        assert job_obj.status == ScanJobStatus.RUNNING.value

        res_events = await session.execute(select(JobEvent).where(JobEvent.scan_job_id == job_id))
        events = res_events.scalars().all()
        assert len(events) == 1


@pytest.mark.anyio
async def test_redirect_approval_state_validation_accepted_and_rejected_states(
    isolated_db_engine: AsyncEngine,
) -> None:
    """Verify approval is accepted ONLY for FAILED + permitted redirect error code + target domain,

    and rejected for COMPLETED, NO_EMAIL, unrelated failures, or missing redirect domain
    with zero counter/state mutations.
    """
    session_factory = async_sessionmaker(
        bind=isolated_db_engine, expire_on_commit=False, class_=AsyncSession
    )

    org_id = uuid.uuid4()
    user_id = uuid.uuid4()
    job_id = uuid.uuid4()

    url_accepted_out_of_scope = uuid.uuid4()
    url_accepted_biz_review = uuid.uuid4()
    url_rejected_completed = uuid.uuid4()
    url_rejected_no_email = uuid.uuid4()
    url_rejected_unrelated_error = uuid.uuid4()
    url_rejected_missing_target = uuid.uuid4()

    async with session_factory() as session:
        async with session.begin():
            org = Organization(
                id=org_id, name="Strict Validation Org", slug=f"org-{org_id.hex[:6]}"
            )
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
                status=ScanJobStatus.COMPLETED_WITH_ERRORS.value,
                total_input_count=6,
                valid_input_count=6,
                completed_count=2,
                failed_count=4,
                queued_count=0,
            )

            # 1. Accepted: FAILED + OUT_OF_SCOPE_REDIRECT + redirect_target_domain
            u1 = ScanURL(
                id=url_accepted_out_of_scope,
                scan_job_id=job_id,
                original_input="https://u1.com",
                normalized_domain="u1.com",
                original_index=0,
                status=ScanURLStatus.FAILED.value,
                attempt_count=1,
                max_attempts=3,
                last_error_code="OUT_OF_SCOPE_REDIRECT",
                redirect_target_domain="target1.com",
            )

            # 2. Accepted: FAILED + BUSINESS_DOMAIN_REDIRECT_REVIEW + redirect_target_domain
            u2 = ScanURL(
                id=url_accepted_biz_review,
                scan_job_id=job_id,
                original_input="https://u2.com",
                normalized_domain="u2.com",
                original_index=1,
                status=ScanURLStatus.FAILED.value,
                attempt_count=1,
                max_attempts=3,
                last_failure_code="BUSINESS_DOMAIN_REDIRECT_REVIEW",
                redirect_target_domain="target2.com",
            )

            # 3. Rejected: COMPLETED status
            u3 = ScanURL(
                id=url_rejected_completed,
                scan_job_id=job_id,
                original_input="https://u3.com",
                normalized_domain="u3.com",
                original_index=2,
                status=ScanURLStatus.COMPLETED.value,
                attempt_count=1,
                max_attempts=3,
                last_error_code="OUT_OF_SCOPE_REDIRECT",
                redirect_target_domain="target3.com",
            )

            # 4. Rejected: NO_EMAIL status
            u4 = ScanURL(
                id=url_rejected_no_email,
                scan_job_id=job_id,
                original_input="https://u4.com",
                normalized_domain="u4.com",
                original_index=3,
                status=ScanURLStatus.NO_EMAIL.value,
                attempt_count=1,
                max_attempts=3,
                last_error_code="OUT_OF_SCOPE_REDIRECT",
                redirect_target_domain="target4.com",
            )

            # 5. Rejected: FAILED status with unrelated error code (DNS_RESOLUTION_FAILED)
            u5 = ScanURL(
                id=url_rejected_unrelated_error,
                scan_job_id=job_id,
                original_input="https://u5.com",
                normalized_domain="u5.com",
                original_index=4,
                status=ScanURLStatus.FAILED.value,
                attempt_count=1,
                max_attempts=3,
                last_error_code="DNS_RESOLUTION_FAILED",
                redirect_target_domain="target5.com",
            )

            # 6. Rejected: FAILED status with permitted code but missing/NULL redirect_target_domain
            u6 = ScanURL(
                id=url_rejected_missing_target,
                scan_job_id=job_id,
                original_input="https://u6.com",
                normalized_domain="u6.com",
                original_index=5,
                status=ScanURLStatus.FAILED.value,
                attempt_count=1,
                max_attempts=3,
                last_error_code="OUT_OF_SCOPE_REDIRECT",
                redirect_target_domain=None,
            )

            session.add_all([org, user, job, u1, u2, u3, u4, u5, u6])

    # Rejection 1: COMPLETED status rejected
    async with session_factory() as session:
        service = ScanJobService(session)
        with pytest.raises(ServiceError) as exc_info:
            await service.approve_url_redirect(org_id, job_id, url_rejected_completed)
        assert exc_info.value.code == ServiceErrorCode.INVALID_RESULT_STATE

    # Rejection 2: NO_EMAIL status rejected
    async with session_factory() as session:
        service = ScanJobService(session)
        with pytest.raises(ServiceError) as exc_info:
            await service.approve_url_redirect(org_id, job_id, url_rejected_no_email)
        assert exc_info.value.code == ServiceErrorCode.INVALID_RESULT_STATE

    # Rejection 3: Unrelated failure code rejected
    async with session_factory() as session:
        service = ScanJobService(session)
        with pytest.raises(ServiceError) as exc_info:
            await service.approve_url_redirect(org_id, job_id, url_rejected_unrelated_error)
        assert exc_info.value.code == ServiceErrorCode.INVALID_RESULT_STATE

    # Rejection 4: Missing redirect_target_domain rejected
    async with session_factory() as session:
        service = ScanJobService(session)
        with pytest.raises(ServiceError) as exc_info:
            await service.approve_url_redirect(org_id, job_id, url_rejected_missing_target)
        assert exc_info.value.code == ServiceErrorCode.INVALID_RESULT_STATE

    # Verify ZERO mutations or events took place for all rejected attempts
    async with session_factory() as session:
        res_job = await session.execute(select(ScanJob).where(ScanJob.id == job_id))
        j = res_job.scalar_one()
        assert j.failed_count == 4
        assert j.queued_count == 0
        assert j.completed_count == 2
        assert j.status == ScanJobStatus.COMPLETED_WITH_ERRORS.value

        res_events = await session.execute(select(JobEvent).where(JobEvent.scan_job_id == job_id))
        assert len(res_events.scalars().all()) == 0

    # Acceptance 1: Approve url1 (OUT_OF_SCOPE_REDIRECT)
    async with session_factory() as session:
        service = ScanJobService(session)
        res1 = await service.approve_url_redirect(org_id, job_id, url_accepted_out_of_scope)
        assert res1.status == ScanURLStatus.QUEUED.value
        assert res1.approved_redirect_domain == "target1.com"

    # Acceptance 2: Approve url2 (BUSINESS_DOMAIN_REDIRECT_REVIEW)
    async with session_factory() as session:
        service = ScanJobService(session)
        res2 = await service.approve_url_redirect(org_id, job_id, url_accepted_biz_review)
        assert res2.status == ScanURLStatus.QUEUED.value
        assert res2.approved_redirect_domain == "target2.com"

    # Verify exact counter invariants after 2 successful approvals
    async with session_factory() as session:
        res_job = await session.execute(select(ScanJob).where(ScanJob.id == job_id))
        j = res_job.scalar_one()
        assert j.failed_count == 2  # 4 initial - 2 approved
        assert j.queued_count == 2  # 0 initial + 2 approved
        assert j.completed_count == 2  # Unchanged
        assert j.status == ScanJobStatus.RUNNING.value  # Transitioned from COMPLETED_WITH_ERRORS

        res_events = await session.execute(select(JobEvent).where(JobEvent.scan_job_id == job_id))
        events = res_events.scalars().all()
        assert len(events) == 2  # Exactly 2 REDIRECT_APPROVED events
