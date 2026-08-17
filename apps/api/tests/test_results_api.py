"""Unit and integration tests for tenant-scoped scan job results, detail, and evidence endpoints."""

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from email_discovery_api.api.dependencies.cursors import encode_cursor
from email_discovery_api.api.dependencies.identity import RequestPrincipal, get_current_principal
from email_discovery_api.api.dependencies.services import get_session_factory
from email_discovery_api.main import app
from email_discovery_api.models import (
    CrawlAttempt,
    CrawledPage,
    EmailEvidence,
    EmailFinding,
    Organization,
    ScanJob,
    ScanURL,
    User,
)
from email_discovery_api.models.enums import ScanJobStatus, ScanURLStatus
from email_discovery_api.repositories.crawl_results import EmailFindingRepository


@pytest.fixture
async def seeded_results_dataset(
    isolated_db_engine: AsyncEngine,
) -> AsyncGenerator[dict[str, Any]]:
    """Seed database with tenant scan job, 5 email findings, and page evidence."""
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
    job_id = uuid.uuid4()
    url_id = uuid.uuid4()
    attempt_id = uuid.uuid4()
    page_id = uuid.uuid4()

    # Foreign tenant
    other_org_id = uuid.uuid4()
    other_user_id = uuid.uuid4()
    other_job_id = uuid.uuid4()

    now = datetime.now(UTC)

    async with session_factory() as session:
        async with session.begin():
            # Other tenant org & job
            other_org = Organization(id=other_org_id, name="Other Org", slug="other-org")
            other_user = User(
                id=other_user_id,
                email="other@other.com",
                normalized_email="other@other.com",
                password_hash="hash",
            )
            other_job = ScanJob(
                id=other_job_id,
                organization_id=other_org_id,
                created_by_user_id=other_user_id,
                status=ScanJobStatus.COMPLETED.value,
                total_input_count=1,
                valid_input_count=1,
            )
            session.add_all([other_org, other_user, other_job])

            # Target tenant org & user & job
            target_org = Organization(id=org_id, name="Target Org", slug="target-org")
            target_user = User(
                id=user_id,
                email="target@test.com",
                normalized_email="target@test.com",
                password_hash="hash",
            )
            job = ScanJob(
                id=job_id,
                organization_id=org_id,
                created_by_user_id=user_id,
                status=ScanJobStatus.COMPLETED.value,
                total_input_count=1,
                valid_input_count=1,
                queued_count=0,
                running_count=0,
                completed_count=1,
                failed_count=0,
                email_finding_count=5,
            )
            url = ScanURL(
                id=url_id,
                scan_job_id=job_id,
                original_index=0,
                original_input="https://example.com",
                normalized_url="https://example.com/",
                normalized_domain="example.com",
                status=ScanURLStatus.COMPLETED.value,
            )
            attempt = CrawlAttempt(
                id=attempt_id,
                scan_url_id=url_id,
                attempt_number=1,
                outcome="FETCHED_AND_PROCESSED",
                requested_url="https://example.com",
                final_url="https://example.com",
                status_code=200,
                started_at=now,
                completed_at=now,
                elapsed_seconds=0.5,
                result_checksum="a" * 64,
            )
            page = CrawledPage(
                id=page_id,
                crawl_attempt_id=attempt_id,
                scan_url_id=url_id,
                normalized_url="https://example.com/",
                final_url="https://example.com/",
                depth=0,
                outcome="FETCHED_AND_PROCESSED",
                status_code=200,
                page_score=100,
                ranking_version="1.0.0",
            )
            session.add_all([target_org, target_user, job, url, attempt, page])

            findings: list[EmailFinding] = []
            emails = [
                ("alice@acme.com", "acme.com", "PERSONAL_OR_NAMED", False),
                ("bob@acme.com", "acme.com", "PERSONAL_OR_NAMED", False),
                ("contact@acme.com", "acme.com", "ROLE_BASED", True),
                ("info@acme.com", "acme.com", "ROLE_BASED", True),
                ("sales@beta.io", "beta.io", "ROLE_BASED", True),
            ]

            for i, (email, domain, cls, role) in enumerate(emails):
                fid = uuid.uuid4()
                f = EmailFinding(
                    id=fid,
                    scan_job_id=job_id,
                    canonical_email=email,
                    email_domain=domain,
                    classification=cls,
                    is_role_based=role,
                    validation_status="UNVERIFIED",
                    first_found_at=now,
                    last_found_at=now + timedelta(minutes=i),
                    evidence_count=2,
                )
                findings.append(f)
                session.add(f)

                # Add evidence
                ev1 = EmailEvidence(
                    email_finding_id=fid,
                    crawled_page_id=page_id,
                    source_type="VISIBLE_TEXT",
                    snippet=f"Contact {email} for details",
                    page_url="https://example.com/",
                    confidence=1.0,
                    candidate_hash=f"a{i}" * 32,
                    created_at=now + timedelta(seconds=i),
                )
                ev2 = EmailEvidence(
                    email_finding_id=fid,
                    crawled_page_id=page_id,
                    source_type="MAILTO_LINK",
                    snippet=f"mailto:{email}",
                    page_url="https://example.com/",
                    confidence=1.0,
                    candidate_hash=f"b{i}" * 32,
                    created_at=now + timedelta(seconds=i + 10),
                )
                session.add_all([ev1, ev2])

    yield {
        "session_factory": session_factory,
        "org_id": org_id,
        "job_id": job_id,
        "other_job_id": other_job_id,
        "first_finding_id": findings[0].id,
    }
    app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_list_results_tenant_isolation_and_404(
    seeded_results_dataset: dict[str, Any],
) -> None:
    """Verify missing job and cross-tenant job return identical 404 error envelope."""
    other_job_id = seeded_results_dataset["other_job_id"]
    non_existent_job_id = uuid.uuid4()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        # Cross-tenant job
        res_other = await client.get(f"/api/v1/scan-jobs/{other_job_id}/results")
        assert res_other.status_code == 404
        assert res_other.json()["error"]["code"] == "JOB_NOT_FOUND"

        # Non-existent job
        res_none = await client.get(f"/api/v1/scan-jobs/{non_existent_job_id}/results")
        assert res_none.status_code == 404
        assert res_none.json()["error"]["code"] == "JOB_NOT_FOUND"


@pytest.mark.anyio
async def test_list_results_keyset_pagination_and_ordering(
    seeded_results_dataset: dict[str, Any],
) -> None:
    """Verify deterministic keyset pagination and ordering (canonical_email ASC, id ASC)."""
    job_id = seeded_results_dataset["job_id"]

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        # Page 1 (limit 2)
        res1 = await client.get(f"/api/v1/scan-jobs/{job_id}/results?limit=2")
        assert res1.status_code == 200
        body1 = res1.json()
        assert len(body1["items"]) == 2
        assert body1["items"][0]["canonical_email"] == "alice@acme.com"
        assert body1["items"][1]["canonical_email"] == "bob@acme.com"
        cursor1 = body1["next_cursor"]
        assert cursor1 is not None

        # Page 2 (limit 2)
        res2 = await client.get(f"/api/v1/scan-jobs/{job_id}/results?limit=2&cursor={cursor1}")
        assert res2.status_code == 200
        body2 = res2.json()
        assert len(body2["items"]) == 2
        assert body2["items"][0]["canonical_email"] == "contact@acme.com"
        assert body2["items"][1]["canonical_email"] == "info@acme.com"


@pytest.mark.anyio
async def test_list_results_filtering(seeded_results_dataset: dict[str, Any]) -> None:
    """Verify filtering by classification, email_domain, and search_prefix."""
    job_id = seeded_results_dataset["job_id"]

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        # Filter classification ROLE_BASED
        res_cls = await client.get(f"/api/v1/scan-jobs/{job_id}/results?classification=ROLE_BASED")
        assert res_cls.status_code == 200
        assert len(res_cls.json()["items"]) == 3

        # Filter domain beta.io
        res_dom = await client.get(f"/api/v1/scan-jobs/{job_id}/results?email_domain=beta.io")
        assert res_dom.status_code == 200
        assert len(res_dom.json()["items"]) == 1
        assert res_dom.json()["items"][0]["canonical_email"] == "sales@beta.io"

        # Search prefix 'al'
        res_pref = await client.get(f"/api/v1/scan-jobs/{job_id}/results?search_prefix=al")
        assert res_pref.status_code == 200
        assert len(res_pref.json()["items"]) == 1
        assert res_pref.json()["items"][0]["canonical_email"] == "alice@acme.com"


@pytest.mark.anyio
async def test_malformed_cursor_rejection(
    seeded_results_dataset: dict[str, Any],
) -> None:
    """Verify malformed base64 or wrong resource cursor returns 400 BAD_REQUEST."""
    job_id = seeded_results_dataset["job_id"]

    bad_resource_cursor = encode_cursor("jobs", [str(datetime.now(UTC)), str(uuid.uuid4())])

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        res1 = await client.get(f"/api/v1/scan-jobs/{job_id}/results?cursor=not-valid-base64!!!")
        assert res1.status_code == 400
        assert res1.json()["error"]["code"] == "BAD_REQUEST"

        res2 = await client.get(f"/api/v1/scan-jobs/{job_id}/results?cursor={bad_resource_cursor}")
        assert res2.status_code == 400
        assert res2.json()["error"]["code"] == "BAD_REQUEST"


@pytest.mark.anyio
async def test_finding_detail_and_evidence_endpoints(
    seeded_results_dataset: dict[str, Any],
) -> None:
    """Verify finding detail endpoint and evidence pagination."""
    job_id = seeded_results_dataset["job_id"]
    finding_id = seeded_results_dataset["first_finding_id"]

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        # Detail endpoint
        res_detail = await client.get(f"/api/v1/scan-jobs/{job_id}/results/{finding_id}")
        assert res_detail.status_code == 200
        body_detail = res_detail.json()
        assert body_detail["finding_id"] == str(finding_id)
        assert len(body_detail["representative_evidence"]) > 0

        # Evidence endpoint
        res_ev = await client.get(f"/api/v1/scan-jobs/{job_id}/results/{finding_id}/evidence")
        assert res_ev.status_code == 200
        body_ev = res_ev.json()
        assert len(body_ev["items"]) == 2


@pytest.mark.anyio
async def test_window_function_evidence_query_count(
    seeded_results_dataset: dict[str, Any],
) -> None:
    """Verify fetching findings uses 1 window query for evidence at the repository level."""
    session_factory = seeded_results_dataset["session_factory"]
    org_id = seeded_results_dataset["org_id"]
    job_id = seeded_results_dataset["job_id"]

    async with session_factory() as session:
        repo = EmailFindingRepository(session)
        findings, _ = await repo.list_findings_keyset(org_id, job_id, limit=5)
        finding_ids = [f.id for f in findings]

        # Call get_representative_evidence_for_findings
        rep_map = await repo.get_representative_evidence_for_findings(
            org_id, job_id, finding_ids, max_per_finding=3
        )
        assert len(rep_map) == 5
        for _fid, ev_list in rep_map.items():
            assert len(ev_list) <= 3


@pytest.mark.anyio
async def test_strict_cursor_invalid_character_categories(
    seeded_results_dataset: dict[str, Any],
) -> None:
    """Verify cursor decoder rejects whitespace, +, /, =, unicode, and naive datetimes."""
    job_id = seeded_results_dataset["job_id"]

    # Base valid cursor
    valid_cursor = encode_cursor("results", ["alice@acme.com", str(uuid.uuid4())])

    invalid_cursors = [
        f" {valid_cursor}",  # Leading space
        f"{valid_cursor} ",  # Trailing space
        f"{valid_cursor}\n",  # Newline
        "eyJ+invalid",  # Base64 '+'
        "eyJ/invalid",  # Base64 '/'
        f"{valid_cursor}=",  # Explicit padding '='
        f"{valid_cursor}🔥",  # Unicode
        # Timezone-naive datetime encoded cursor
        encode_cursor("evidence", ["2026-08-16T14:00:00", str(uuid.uuid4())]),
    ]

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        for bad_cursor in invalid_cursors:
            res = await client.get(
                f"/api/v1/scan-jobs/{job_id}/results", params={"cursor": bad_cursor}
            )
            assert res.status_code == 400, f"Expected 400 for cursor {bad_cursor!r}"
            assert res.json()["error"]["code"] == "BAD_REQUEST"


@pytest.mark.anyio
async def test_validation_status_filter_accepted_and_rejected(
    seeded_results_dataset: dict[str, Any],
) -> None:
    """Verify validation_status enum filter accepts valid statuses and rejects invalid values."""
    job_id = seeded_results_dataset["job_id"]

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        # Valid enum value UNVERIFIED
        res_valid = await client.get(
            f"/api/v1/scan-jobs/{job_id}/results?validation_status=UNVERIFIED"
        )
        assert res_valid.status_code == 200
        assert len(res_valid.json()["items"]) == 5

        # Invalid enum value
        res_invalid = await client.get(
            f"/api/v1/scan-jobs/{job_id}/results?validation_status=INVALID_STATUS_NAME"
        )
        assert res_invalid.status_code == 422
        assert res_invalid.json()["error"]["code"] == "UNPROCESSABLE_ENTITY"


@pytest.mark.anyio
async def test_strict_search_prefix_validation(
    seeded_results_dataset: dict[str, Any],
) -> None:
    """Verify search_prefix rejects short, wildcards %, _, backslash, and controls."""
    job_id = seeded_results_dataset["job_id"]

    invalid_prefixes = [
        "   ",  # Whitespace-only
        " a ",  # Post-trim short (len 1)
        "info%",  # SQL wildcard %
        "info_tech",  # SQL wildcard _
        "info\\admin",  # Backslash
        "info\n",  # Control character
        "info🔥",  # Unicode
    ]

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        for bad_prefix in invalid_prefixes:
            res = await client.get(
                f"/api/v1/scan-jobs/{job_id}/results", params={"search_prefix": bad_prefix}
            )
            assert res.status_code == 400, f"Expected 400 for prefix {bad_prefix!r}"
            assert res.json()["error"]["code"] == "BAD_REQUEST"

        # Valid prefix
        res_ok = await client.get(f"/api/v1/scan-jobs/{job_id}/results?search_prefix=contact")
        assert res_ok.status_code == 200
        assert len(res_ok.json()["items"]) == 1
        assert res_ok.json()["items"][0]["canonical_email"] == "contact@acme.com"


@pytest.mark.anyio
async def test_representative_evidence_deterministic_ordering_repeatability(
    seeded_results_dataset: dict[str, Any],
) -> None:
    """Verify representative evidence queries produce identical deterministic ordering."""
    session_factory = seeded_results_dataset["session_factory"]
    org_id = seeded_results_dataset["org_id"]
    job_id = seeded_results_dataset["job_id"]

    async with session_factory() as session:
        repo = EmailFindingRepository(session)
        findings, _ = await repo.list_findings_keyset(org_id, job_id, limit=5)
        finding_ids = [f.id for f in findings]

        run1 = await repo.get_representative_evidence_for_findings(
            org_id, job_id, finding_ids, max_per_finding=3
        )
        run2 = await repo.get_representative_evidence_for_findings(
            org_id, job_id, finding_ids, max_per_finding=3
        )

        assert list(run1.keys()) == list(run2.keys())
        for fid in finding_ids:
            list1_ids = [ev.id for ev in run1[fid]]
            list2_ids = [ev.id for ev in run2[fid]]
            assert list1_ids == list2_ids
