"""Persistence, migration, CSV, and tenant isolation tests for Phase 4A primary email selection."""

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from email_discovery_api.models.email_finding import EmailFinding
from email_discovery_api.models.organization import Organization
from email_discovery_api.models.scan_job import ScanJob
from email_discovery_api.models.scan_url import ScanURL
from email_discovery_api.services.results import ScanJobResultsService


@pytest.fixture
async def db_session(isolated_db_engine: AsyncEngine) -> AsyncGenerator[AsyncSession]:
    session_factory = async_sessionmaker(
        bind=isolated_db_engine,
        expire_on_commit=False,
        class_=AsyncSession,
    )
    async with session_factory() as session:
        yield session


@pytest.mark.anyio
async def test_db_partial_unique_index_blocks_concurrent_duplicate(
    db_session: AsyncSession,
) -> None:
    """Verify uq_email_findings_scan_url_not_null prevents multiple findings for one ScanURL."""
    org_id = uuid.uuid4()
    job_id = uuid.uuid4()
    scan_url_id = uuid.uuid4()
    now = datetime.now(UTC)

    org = Organization(id=org_id, name="Test Org", slug=f"test-org-{org_id.hex[:6]}")
    db_session.add(org)
    await db_session.flush()

    job = ScanJob(id=job_id, organization_id=org_id, name="Job 1", status="RUNNING")
    db_session.add(job)
    await db_session.flush()

    surl = ScanURL(
        id=scan_url_id,
        scan_job_id=job_id,
        original_index=0,
        original_input="https://example.com",
        status="SCANNING",
    )
    db_session.add(surl)
    await db_session.flush()

    f1 = EmailFinding(
        scan_job_id=job_id,
        scan_url_id=scan_url_id,
        canonical_email="contact@example.com",
        email_domain="example.com",
        classification="ROLE_BASED",
        is_role_based=True,
        validation_status="UNVERIFIED",
        first_found_at=now,
        last_found_at=now,
    )
    db_session.add(f1)
    await db_session.flush()

    f2 = EmailFinding(
        scan_job_id=job_id,
        scan_url_id=scan_url_id,
        canonical_email="info@example.com",
        email_domain="example.com",
        classification="ROLE_BASED",
        is_role_based=True,
        validation_status="UNVERIFIED",
        first_found_at=now,
        last_found_at=now,
    )
    db_session.add(f2)

    with pytest.raises(IntegrityError):
        await db_session.flush()

    await db_session.rollback()


@pytest.mark.anyio
async def test_two_scan_urls_same_job_can_insert_same_email(db_session: AsyncSession) -> None:
    """Verify two distinct ScanURLs in the same job can each insert the same canonical email."""
    org_id = uuid.uuid4()
    job_id = uuid.uuid4()
    url_id_1 = uuid.uuid4()
    url_id_2 = uuid.uuid4()
    now = datetime.now(UTC)

    org = Organization(id=org_id, name="Test Org 2", slug=f"test-org-{org_id.hex[:6]}")
    db_session.add(org)
    await db_session.flush()

    job = ScanJob(id=job_id, organization_id=org_id, name="Job 2", status="RUNNING")
    db_session.add(job)
    await db_session.flush()

    surl1 = ScanURL(
        id=url_id_1,
        scan_job_id=job_id,
        original_index=0,
        original_input="https://site1.com",
        status="COMPLETED",
    )
    surl2 = ScanURL(
        id=url_id_2,
        scan_job_id=job_id,
        original_index=1,
        original_input="https://site2.com",
        status="COMPLETED",
    )
    db_session.add_all([surl1, surl2])
    await db_session.flush()

    f1 = EmailFinding(
        scan_job_id=job_id,
        scan_url_id=url_id_1,
        canonical_email="agency@shared.com",
        email_domain="shared.com",
        classification="ROLE_BASED",
        is_role_based=True,
        validation_status="UNVERIFIED",
        first_found_at=now,
        last_found_at=now,
    )
    f2 = EmailFinding(
        scan_job_id=job_id,
        scan_url_id=url_id_2,
        canonical_email="agency@shared.com",
        email_domain="shared.com",
        classification="ROLE_BASED",
        is_role_based=True,
        validation_status="UNVERIFIED",
        first_found_at=now,
        last_found_at=now,
    )
    db_session.add_all([f1, f2])
    await db_session.flush()

    res = await db_session.execute(select(EmailFinding).where(EmailFinding.scan_job_id == job_id))
    findings = res.scalars().all()
    assert len(findings) == 2
    assert {f.scan_url_id for f in findings} == {url_id_1, url_id_2}


@pytest.mark.anyio
async def test_composite_fk_prevents_cross_job_scan_url(db_session: AsyncSession) -> None:
    """Verify composite FK prevents linking a ScanURL from job A to a finding in job B."""
    org_id = uuid.uuid4()
    job_id_a = uuid.uuid4()
    job_id_b = uuid.uuid4()
    url_id_a = uuid.uuid4()
    now = datetime.now(UTC)

    org = Organization(id=org_id, name="Test Org 3", slug=f"test-org-{org_id.hex[:6]}")
    db_session.add(org)
    await db_session.flush()

    job_a = ScanJob(id=job_id_a, organization_id=org_id, name="Job A", status="RUNNING")
    job_b = ScanJob(id=job_id_b, organization_id=org_id, name="Job B", status="RUNNING")
    db_session.add_all([job_a, job_b])
    await db_session.flush()

    surl_a = ScanURL(
        id=url_id_a,
        scan_job_id=job_id_a,
        original_index=0,
        original_input="https://sitea.com",
        status="COMPLETED",
    )
    db_session.add(surl_a)
    await db_session.flush()

    # Attempt finding in job B referencing surl_a (which belongs to job A)
    mismatched = EmailFinding(
        scan_job_id=job_id_b,
        scan_url_id=url_id_a,
        canonical_email="info@sitea.com",
        email_domain="sitea.com",
        classification="ROLE_BASED",
        is_role_based=True,
        validation_status="UNVERIFIED",
        first_found_at=now,
        last_found_at=now,
    )
    db_session.add(mismatched)

    with pytest.raises(IntegrityError):
        await db_session.flush()

    await db_session.rollback()


@pytest.mark.anyio
async def test_historical_null_scan_url_serialization(db_session: AsyncSession) -> None:
    """Verify historical findings with scan_url_id IS NULL serialize safely."""
    org_id = uuid.uuid4()
    job_id = uuid.uuid4()
    now = datetime.now(UTC)

    org = Organization(id=org_id, name="Test Org 4", slug=f"test-org-{org_id.hex[:6]}")
    db_session.add(org)
    await db_session.flush()

    job = ScanJob(id=job_id, organization_id=org_id, name="Job Historical", status="COMPLETED")
    db_session.add(job)
    await db_session.flush()

    hist_f = EmailFinding(
        scan_job_id=job_id,
        scan_url_id=None,
        canonical_email="legacy@old.com",
        email_domain="old.com",
        classification="ROLE_BASED",
        is_role_based=True,
        validation_status="UNVERIFIED",
        first_found_at=now,
        last_found_at=now,
    )
    db_session.add(hist_f)
    await db_session.flush()

    service = ScanJobResultsService(db_session)
    findings, _, _ = await service.list_results(org_id, job_id)
    assert len(findings) == 1
    assert findings[0].scan_url_id is None
    assert findings[0].canonical_email == "legacy@old.com"
