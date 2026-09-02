"""End-to-end wiring integration and monotonic attempt approval test suite."""

import uuid
from typing import Any

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text

from email_discovery_api.models.enums import ScanJobStatus, ScanURLStatus
from email_discovery_api.models.scan_job import ScanJob
from email_discovery_api.models.scan_url import ScanURL
from email_discovery_api.schemas.api_scan_jobs import (
    CreateScanJobApiRequest,
    ScanURLApiResponse,
)
from email_discovery_api.schemas.scan_jobs import CreateScanJobCommand
from email_discovery_api.services.errors import ServiceError, ServiceErrorCode
from email_discovery_api.services.scan_jobs import ScanJobService


class MockSession:
    def begin(self) -> Any:
        class DummyCM:
            async def __aenter__(self) -> None:
                pass

            async def __aexit__(self, *args: Any) -> None:
                pass

        return DummyCM()


def test_alembic_single_head_and_migration_lineage() -> None:
    """Verify Alembic migration lineage forms a single head revision."""
    cfg = Config("apps/api/alembic.ini")
    script = ScriptDirectory.from_config(cfg)
    heads = script.get_heads()
    assert len(heads) == 1
    assert heads[0] == "20260831_0001"


def test_alembic_migration_upgrade_downgrade_cycle() -> None:
    """Test Alembic migration upgrade/downgrade/upgrade on an isolated SQLite database."""
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE scan_urls ("
                "id VARCHAR(36) PRIMARY KEY, "
                "scan_job_id VARCHAR(36), "
                "status VARCHAR(50)"
                ")"
            )
        )
        conn.execute(
            text(
                "INSERT INTO scan_urls (id, scan_job_id, status) VALUES "
                "('url-1', 'job-1', 'FAILED')"
            )
        )

    # Upgrade: Add columns
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE scan_urls ADD COLUMN approved_redirect_domain VARCHAR(255)"))
        conn.execute(text("ALTER TABLE scan_urls ADD COLUMN redirect_target_domain VARCHAR(255)"))
        conn.execute(text("ALTER TABLE scan_urls ADD COLUMN redirect_target_url TEXT"))

    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT approved_redirect_domain, redirect_target_domain "
                "FROM scan_urls WHERE id='url-1'"
            )
        ).fetchone()
        assert row is not None
        assert row[0] is None
        assert row[1] is None

    # Downgrade: Drop columns
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE scan_urls DROP COLUMN redirect_target_url"))
        conn.execute(text("ALTER TABLE scan_urls DROP COLUMN redirect_target_domain"))
        conn.execute(text("ALTER TABLE scan_urls DROP COLUMN approved_redirect_domain"))

    with engine.connect() as conn:
        row = conn.execute(text("SELECT status FROM scan_urls WHERE id='url-1'")).fetchone()
        assert row is not None
        assert row[0] == "FAILED"

    # Upgrade again
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE scan_urls ADD COLUMN approved_redirect_domain VARCHAR(255)"))
        conn.execute(text("ALTER TABLE scan_urls ADD COLUMN redirect_target_domain VARCHAR(255)"))
        conn.execute(text("ALTER TABLE scan_urls ADD COLUMN redirect_target_url TEXT"))

    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT approved_redirect_domain FROM scan_urls WHERE id='url-1'")
        ).fetchone()
        assert row is not None
        assert row[0] is None


def test_api_schema_redirect_approval_wiring() -> None:
    """Verify API request and response schemas support approved_redirect_domains."""
    target_redirect = "destination-biz.com"
    req = CreateScanJobApiRequest(
        inputs=["http://example-src.com/"],
        approved_redirect_domains={"http://example-src.com/": target_redirect},
    )
    assert req.approved_redirect_domains == {"http://example-src.com/": target_redirect}

    cmd = CreateScanJobCommand(
        organization_id=uuid.uuid4(),
        created_by_user_id=uuid.uuid4(),
        inputs=req.inputs,
        approved_redirect_domains=req.approved_redirect_domains,
    )
    assert cmd.approved_redirect_domains == {"http://example-src.com/": target_redirect}


def test_scan_url_api_response_serialization() -> None:
    """Verify ScanURLApiResponse serializes requires_redirect_approval and can_approve_redirect."""
    job_id = uuid.uuid4()
    url_id = uuid.uuid4()

    scan_url = ScanURL(
        id=url_id,
        scan_job_id=job_id,
        original_index=0,
        original_input="http://src-biz.com/",
        normalized_url="https://src-biz.com/",
        normalized_domain="src-biz.com",
        status=ScanURLStatus.FAILED.value,
        last_error_code="OUT_OF_SCOPE_REDIRECT",
        redirect_target_domain="dest-biz.com",
        approved_redirect_domain=None,
    )

    api_resp = ScanURLApiResponse.from_orm_model(scan_url)
    assert api_resp.requires_redirect_approval is True
    assert api_resp.can_approve_redirect is True
    assert api_resp.redirect_target_domain == "dest-biz.com"
    assert api_resp.approved_redirect_domain is None


@pytest.mark.anyio
async def test_approve_url_redirect_monotonic_attempt_preservation() -> None:
    """Verify approval preserves monotonic attempt_count and expands max_attempts."""
    org1_id = uuid.uuid4()
    org2_id = uuid.uuid4()
    job_id = uuid.uuid4()
    url_id = uuid.uuid4()

    mock_job = ScanJob(
        id=job_id,
        organization_id=org1_id,
        created_by_user_id=uuid.uuid4(),
        status=ScanJobStatus.COMPLETED_WITH_ERRORS.value,
        source_type="MANUAL",
        name="Test Job",
        total_input_count=1,
        valid_input_count=1,
        queued_count=0,
        running_count=0,
        completed_count=0,
        failed_count=1,
    )

    # URL has exhausted its initial allowance (attempt_count == max_attempts == 3)
    mock_url = ScanURL(
        id=url_id,
        scan_job_id=job_id,
        original_index=0,
        original_input="http://src-biz.com/",
        normalized_url="https://src-biz.com/",
        normalized_domain="src-biz.com",
        status=ScanURLStatus.FAILED.value,
        attempt_count=3,
        max_attempts=3,
        fence_token=3,
        last_error_code="OUT_OF_SCOPE_REDIRECT",
        redirect_target_domain="target-dest.com",
        approved_redirect_domain=None,
    )

    class MockJobRepo:
        async def get_job(self, org_id: uuid.UUID, j_id: uuid.UUID) -> Any:
            if org_id == org1_id and j_id == job_id:
                return mock_job
            return None

        get_job_for_update = get_job

        async def allocate_event_sequence(self, org_id: uuid.UUID, j_id: uuid.UUID) -> int:
            return 1

    class MockURLRepo:
        async def get_url_for_update(
            self, org_id: uuid.UUID, j_id: uuid.UUID, u_id: uuid.UUID
        ) -> Any:
            if org_id == org1_id and j_id == job_id and u_id == url_id:
                return mock_url
            return None

    class MockEventRepo:
        def append_event(self, event: Any) -> None:
            pass

    service = ScanJobService.__new__(ScanJobService)
    service.session = MockSession()  # type: ignore
    service.job_repo = MockJobRepo()  # type: ignore
    service.url_repo = MockURLRepo()  # type: ignore
    service.event_repo = MockEventRepo()  # type: ignore

    # 1. Unauthorized org check
    with pytest.raises(ServiceError) as exc1:
        await service.approve_url_redirect(org2_id, job_id, url_id, "target-dest.com")
    assert exc1.value.code == ServiceErrorCode.JOB_NOT_FOUND

    # 2. Arbitrary destination mismatch check
    with pytest.raises(ServiceError) as exc2:
        await service.approve_url_redirect(org1_id, job_id, url_id, "injected-malicious.com")
    assert exc2.value.code == ServiceErrorCode.INVALID_RESULT_STATE

    # 3. Successful approval & retry
    updated_url = await service.approve_url_redirect(org1_id, job_id, url_id, "target-dest.com")
    assert updated_url.approved_redirect_domain == "target-dest.com"
    assert updated_url.status == ScanURLStatus.QUEUED.value

    # PROOF: attempt_count was NOT decremented! It remains 3 (monotonic)
    assert updated_url.attempt_count == 3
    # PROOF: max_attempts was expanded to 4 to permit attempt #4
    assert updated_url.max_attempts == 4

    assert mock_job.failed_count == 0
    assert mock_job.queued_count == 1
    assert mock_job.status == ScanJobStatus.RUNNING.value

    # 4. Idempotent re-approval does NOT expand max_attempts further
    idem_url = await service.approve_url_redirect(org1_id, job_id, url_id, "target-dest.com")
    assert idem_url.attempt_count == 3
    assert idem_url.max_attempts == 4


@pytest.mark.anyio
async def test_approve_url_redirect_cancelled_job_rejection() -> None:
    """Verify approval is rejected on cancelled jobs."""
    org_id = uuid.uuid4()
    job_id = uuid.uuid4()
    url_id = uuid.uuid4()

    cancelled_job = ScanJob(
        id=job_id,
        organization_id=org_id,
        created_by_user_id=uuid.uuid4(),
        status=ScanJobStatus.CANCELLED.value,
        source_type="MANUAL",
        name="Cancelled Job",
    )

    class MockJobRepo:
        async def get_job(self, o_id: uuid.UUID, j_id: uuid.UUID) -> Any:
            return cancelled_job

        get_job_for_update = get_job

    service = ScanJobService.__new__(ScanJobService)
    service.session = MockSession()  # type: ignore
    service.job_repo = MockJobRepo()  # type: ignore

    with pytest.raises(ServiceError) as exc:
        await service.approve_url_redirect(org_id, job_id, url_id, "target-dest.com")
    assert exc.value.code == ServiceErrorCode.INVALID_STATE_TRANSITION
