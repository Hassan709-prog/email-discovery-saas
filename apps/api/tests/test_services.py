"""Unit tests for ScanJobService, input previewing, policies, and progress."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from email_discovery_api.models.enums import MembershipRole, ScanJobStatus
from email_discovery_api.models.membership import Membership
from email_discovery_api.models.organization import Organization
from email_discovery_api.models.scan_job import ScanJob
from email_discovery_api.schemas.scan_jobs import CreateScanJobCommand, ScanJobProgress
from email_discovery_api.services.errors import ServiceError, ServiceErrorCode
from email_discovery_api.services.policies import ScanCreationPolicy
from email_discovery_api.services.scan_jobs import (
    ScanJobService,
    compute_request_fingerprint,
    preview_scan_inputs,
)


def test_fingerprint_dict_independence_and_input_sensitivity() -> None:
    """Verify request fingerprint is dictionary-order independent and input-order sensitive."""
    org_id = uuid.uuid4()
    user_id = uuid.uuid4()

    cmd1 = CreateScanJobCommand(
        organization_id=org_id,
        created_by_user_id=user_id,
        inputs=["https://example.com", "https://acme.org"],
        configuration_snapshot={"b": 2, "a": 1},
    )
    cmd2 = CreateScanJobCommand(
        organization_id=org_id,
        created_by_user_id=user_id,
        inputs=["https://example.com", "https://acme.org"],
        configuration_snapshot={"a": 1, "b": 2},
    )
    cmd_diff_order = CreateScanJobCommand(
        organization_id=org_id,
        created_by_user_id=user_id,
        inputs=["https://acme.org", "https://example.com"],
        configuration_snapshot={"a": 1, "b": 2},
    )

    targets1 = ["https://example.com/", "https://acme.org/"]
    targets_diff = ["https://acme.org/", "https://example.com/"]

    fp1 = compute_request_fingerprint(cmd1, targets1)
    fp2 = compute_request_fingerprint(cmd2, targets1)
    fp_diff = compute_request_fingerprint(cmd_diff_order, targets_diff)

    assert fp1 == fp2
    assert fp1 != fp_diff
    assert len(fp1) == 64


def test_preview_scan_inputs_classification() -> None:
    """Verify input previewing classifies valid, invalid, and intra-job duplicate URLs correctly."""
    inputs = [
        "https://example.com",
        "ftp://example.com",
        "https://example.com",
        "https://acme.org",
    ]
    batch_result = preview_scan_inputs(inputs)

    assert batch_result.total_input_count == 4
    items = batch_result.items
    assert items[0].decision_code == "READY_TO_CHECK"
    assert items[0].canonical_target == "https://example.com/"
    assert items[1].decision_code == "UNSUPPORTED_SCHEME"
    assert items[2].decision_code == "DUPLICATE_URL"
    assert items[2].duplicate_of_index == 0
    assert items[3].decision_code == "READY_TO_CHECK"


def test_policy_pre_ingestion_limits() -> None:
    """Verify ScanCreationPolicy rejects oversized jobs before database row creation."""
    policy = ScanCreationPolicy(
        max_inputs_per_job=2,
        max_input_length=50,
        max_configuration_json_bytes=100,
    )

    # 1. Exceed input count
    with pytest.raises(ServiceError) as exc:
        policy.validate_pre_ingestion(["a", "b", "c"], {})
    assert exc.value.code is ServiceErrorCode.INPUT_LIMIT_EXCEEDED

    # 2. Exceed single input character length
    with pytest.raises(ServiceError) as exc:
        policy.validate_pre_ingestion(["https://" + "x" * 100], {})
    assert exc.value.code is ServiceErrorCode.INPUT_TOO_LONG

    # 3. Exceed configuration JSON size
    with pytest.raises(ServiceError) as exc:
        policy.validate_pre_ingestion(["https://example.com"], {"data": "x" * 500})
    assert exc.value.code is ServiceErrorCode.CONFIGURATION_TOO_LARGE


@pytest.mark.anyio
async def test_create_job_authorization_failures() -> None:
    """Verify job creation rejects missing organizations or viewer role users."""
    session = MagicMock(spec=AsyncSession)
    service = ScanJobService(session)
    org_id = uuid.uuid4()
    user_id = uuid.uuid4()

    cmd = CreateScanJobCommand(
        organization_id=org_id,
        created_by_user_id=user_id,
        inputs=["https://example.com"],
    )

    # 1. Missing organization
    service.org_repo.get_active_organization_for_update = AsyncMock(return_value=None)
    with pytest.raises(ServiceError) as exc:
        await service.create_job(cmd)
    assert exc.value.code is ServiceErrorCode.ORGANIZATION_NOT_FOUND

    # 2. Viewer role user
    service.org_repo.get_active_organization_for_update = AsyncMock(
        return_value=Organization(id=org_id, name="Acme", slug="acme")
    )
    service.org_repo.get_active_membership = AsyncMock(
        return_value=Membership(
            organization_id=org_id, user_id=user_id, role=MembershipRole.VIEWER.value
        )
    )
    with pytest.raises(ServiceError) as exc:
        await service.create_job(cmd)
    assert exc.value.code is ServiceErrorCode.USER_NOT_AUTHORIZED


@pytest.mark.anyio
async def test_create_job_success_workflow() -> None:
    """Verify job creation adds ScanJob, ScanURLs, allocates sequence 1, and appends event."""
    session = MagicMock(spec=AsyncSession)
    service = ScanJobService(session)
    org_id = uuid.uuid4()
    user_id = uuid.uuid4()

    cmd = CreateScanJobCommand(
        organization_id=org_id,
        created_by_user_id=user_id,
        inputs=["https://example.com", "https://example.com"],
    )

    service.org_repo.get_active_organization_for_update = AsyncMock(
        return_value=Organization(id=org_id, name="Acme", slug="acme")
    )
    service.org_repo.get_active_membership = AsyncMock(
        return_value=Membership(
            organization_id=org_id, user_id=user_id, role=MembershipRole.OWNER.value
        )
    )
    service.job_repo.count_active_jobs = AsyncMock(return_value=0)
    service.job_repo.allocate_event_sequence = AsyncMock(return_value=1)
    service.job_repo.add_job = MagicMock()
    service.url_repo.add_scan_urls = MagicMock()
    service.event_repo.append_event = MagicMock()

    res = await service.create_job(cmd)

    assert res.job.total_input_count == 2
    assert res.job.valid_input_count == 1
    assert res.job.duplicate_input_count == 1
    assert service.job_repo.add_job.call_count == 1
    assert service.url_repo.add_scan_urls.call_count == 1
    assert service.event_repo.append_event.call_count == 1


@pytest.mark.anyio
async def test_idempotency_matching_and_conflict() -> None:
    """Verify idempotency key matching returns existing job or raises IDEMPOTENCY_CONFLICT."""
    session = MagicMock(spec=AsyncSession)
    service = ScanJobService(session)
    org_id = uuid.uuid4()
    user_id = uuid.uuid4()

    cmd = CreateScanJobCommand(
        organization_id=org_id,
        created_by_user_id=user_id,
        inputs=["https://example.com"],
        idempotency_key="key-123",
    )
    fp = compute_request_fingerprint(cmd, ["https://example.com/"])

    existing_job = ScanJob(
        id=uuid.uuid4(),
        organization_id=org_id,
        idempotency_key="key-123",
        request_fingerprint=fp,
        status=ScanJobStatus.DRAFT.value,
    )

    service.org_repo.get_active_organization_for_update = AsyncMock(
        return_value=Organization(id=org_id, name="Acme", slug="acme")
    )
    service.org_repo.get_active_membership = AsyncMock(
        return_value=Membership(
            organization_id=org_id, user_id=user_id, role=MembershipRole.OWNER.value
        )
    )
    service.job_repo.count_active_jobs = AsyncMock(return_value=0)
    service.job_repo.find_by_idempotency_key = AsyncMock(return_value=existing_job)

    # 1. Same fingerprint -> returns existing job
    res = await service.create_job(cmd)
    assert res.job.id == existing_job.id

    # 2. Different fingerprint -> raises IDEMPOTENCY_CONFLICT
    existing_job.request_fingerprint = "different-fp-123"
    with pytest.raises(ServiceError) as exc:
        await service.create_job(cmd)
    assert exc.value.code is ServiceErrorCode.IDEMPOTENCY_CONFLICT


@pytest.mark.anyio
async def test_uniqueness_race_recovery_on_integrity_error() -> None:
    """Verify IntegrityError uniqueness race recovery re-reads job in fresh transaction."""
    session = MagicMock(spec=AsyncSession)
    service = ScanJobService(session)
    org_id = uuid.uuid4()
    user_id = uuid.uuid4()

    cmd = CreateScanJobCommand(
        organization_id=org_id,
        created_by_user_id=user_id,
        inputs=["https://example.com"],
        idempotency_key="key-race",
    )
    fp = compute_request_fingerprint(cmd, ["https://example.com/"])

    existing_job = ScanJob(
        id=uuid.uuid4(),
        organization_id=org_id,
        idempotency_key="key-race",
        request_fingerprint=fp,
        status=ScanJobStatus.DRAFT.value,
    )

    # Simulate IntegrityError raised during job creation
    service._create_job_in_transaction = AsyncMock(  # type: ignore[reportPrivateUsage]
        side_effect=IntegrityError("stmt", {}, Exception("unique constraint violation"))
    )
    service.job_repo.find_by_idempotency_key = AsyncMock(return_value=existing_job)

    recovered = await service.create_job(cmd)
    assert recovered.job.id == existing_job.id
    assert service.job_repo.find_by_idempotency_key.call_count == 1


@pytest.mark.anyio
async def test_state_transitions_allowed_and_rejected() -> None:
    """Verify allowed and disallowed state transitions."""
    session = MagicMock(spec=AsyncSession)
    service = ScanJobService(session)
    org_id = uuid.uuid4()
    job_id = uuid.uuid4()

    job = ScanJob(
        id=job_id,
        organization_id=org_id,
        status=ScanJobStatus.DRAFT.value,
    )
    service.job_repo.get_job = AsyncMock(return_value=job)
    service.job_repo.update_job_status_conditional = AsyncMock(return_value=True)
    service.job_repo.allocate_event_sequence = AsyncMock(return_value=2)
    service.event_repo.append_event = MagicMock()

    # Disallowed transition: DRAFT -> RUNNING
    with pytest.raises(ServiceError) as exc:
        await service.transition_job_status(org_id, job_id, ScanJobStatus.RUNNING)
    assert exc.value.code is ServiceErrorCode.INVALID_STATE_TRANSITION

    # Allowed transition: DRAFT -> QUEUED
    updated = await service.transition_job_status(org_id, job_id, ScanJobStatus.QUEUED)
    assert updated is not None


def test_progress_calculation_zero_inputs_and_clamping() -> None:
    """Verify progress calculation handles zero valid inputs and clamps percentage."""
    now = datetime.now(UTC)
    job_id = uuid.uuid4()

    # Zero valid inputs, DRAFT status -> 0.0%
    prog0 = ScanJobProgress.from_counts(
        job_id=job_id,
        status=ScanJobStatus.DRAFT,
        total_input_count=0,
        valid_input_count=0,
        duplicate_input_count=0,
        queued_count=0,
        running_count=0,
        completed_count=0,
        failed_count=0,
        email_finding_count=0,
        created_at=now,
        started_at=None,
        completed_at=None,
    )
    assert prog0.progress_percentage == 0.0
    assert prog0.invalid_input_count == 0

    # Populated inputs: 5 completed out of 10 valid -> 50.0%
    prog50 = ScanJobProgress.from_counts(
        job_id=job_id,
        status=ScanJobStatus.RUNNING,
        total_input_count=12,
        valid_input_count=10,
        duplicate_input_count=2,
        queued_count=5,
        running_count=0,
        completed_count=5,
        failed_count=0,
        email_finding_count=3,
        created_at=now,
        started_at=now,
        completed_at=None,
    )
    assert prog50.progress_percentage == 50.0
    assert prog50.invalid_input_count == 0
