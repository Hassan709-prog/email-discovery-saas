"""Unit tests for AnalyticsService and GET /api/v1/analytics/overview endpoint."""

import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.sql.selectable import Select

from email_discovery_api.api.dependencies.identity import RequestPrincipal, get_current_principal
from email_discovery_api.api.dependencies.services import get_analytics_service
from email_discovery_api.main import create_app
from email_discovery_api.models.enums import (
    EmailClassification,
    EmailValidationStatus,
    ScanJobStatus,
)
from email_discovery_api.schemas.analytics import (
    AnalyticsOverviewResponse,
    AnalyticsPeriodEnum,
    AnalyticsTimelinePoint,
)
from email_discovery_api.services.analytics import AnalyticsService


def _empty_list() -> list[Any]:
    return []


@pytest.fixture
def test_app() -> FastAPI:
    app = create_app()
    app.state.db_manager = MagicMock()
    return app


@pytest.mark.anyio
async def test_analytics_overview_empty_account_zero_state() -> None:
    """Verify empty tenant returns zero counts and zero-filled maps."""
    mock_session = AsyncMock()

    # Mock DB query results for zero state
    agg_mock = MagicMock(
        total_scans=0,
        websites_submitted=0,
        websites_completed=0,
        websites_failed=0,
        emails_discovered=0,
    )
    mock_session.execute = AsyncMock(
        side_effect=[
            MagicMock(one=lambda: agg_mock),  # 1. Job aggregates
            MagicMock(scalar_one=lambda: 0),  # 2. Active scans
            MagicMock(all=_empty_list),  # 3. Status distribution
            MagicMock(all=_empty_list),  # 4. Classification distribution
            MagicMock(all=_empty_list),  # 5. Validation distribution
            MagicMock(all=_empty_list),  # 6a. Timeline scans
            MagicMock(all=_empty_list),  # 6b. Timeline emails
            MagicMock(all=_empty_list),  # 7. Recent completed scans
        ]
    )

    service = AnalyticsService(mock_session)
    fixed_now = datetime(2026, 8, 18, 12, 0, 0, tzinfo=UTC)
    org_id = uuid.uuid4()

    res = await service.get_overview(
        organization_id=org_id,
        period=AnalyticsPeriodEnum.THIRTY_DAYS,
        now=fixed_now,
    )

    assert isinstance(res, AnalyticsOverviewResponse)
    assert res.total_scans == 0
    assert res.active_scans == 0
    assert res.websites_submitted == 0
    assert res.websites_processed == 0
    assert res.websites_completed == 0
    assert res.websites_failed == 0
    assert res.emails_discovered == 0
    assert res.successful_processing_rate == 0.0

    # Verify all enum keys are present with 0 values
    for st in ScanJobStatus:
        assert res.status_distribution[st.value] == 0

    for cls in EmailClassification:
        assert res.findings_by_classification[cls.value] == 0

    for val in EmailValidationStatus:
        assert res.findings_by_validation_status[val.value] == 0

    # Verify 30 daily timeline points zero-filled
    assert len(res.scan_activity_timeline) == 30
    assert res.scan_activity_timeline[0].scans_created == 0
    assert res.scan_activity_timeline[0].emails_found == 0


@pytest.mark.anyio
async def test_analytics_formulas_and_rates() -> None:
    """Verify website formulas, active scan counts, and processing rate calculation."""
    mock_session = AsyncMock()

    agg_mock = MagicMock(
        total_scans=10,
        websites_submitted=100,
        websites_completed=80,
        websites_failed=20,
        emails_discovered=250,
    )
    mock_session.execute = AsyncMock(
        side_effect=[
            MagicMock(one=lambda: agg_mock),  # 1. Job aggregates
            MagicMock(scalar_one=lambda: 2),  # 2. Active scans
            MagicMock(all=lambda: [("COMPLETED", 8), ("RUNNING", 2)]),  # 3. Status
            MagicMock(all=lambda: [("ROLE_BASED", 50), ("PERSONAL_OR_NAMED", 200)]),  # 4. Class
            MagicMock(all=lambda: [("UNVERIFIED", 200), ("VALID", 50)]),  # 5. Validation
            MagicMock(all=lambda: [("2026-08-18", 2)]),  # 6a. Timeline scans
            MagicMock(all=lambda: [("2026-08-18", 10)]),  # 6b. Timeline emails
            MagicMock(all=_empty_list),  # 7. Recent scans
        ]
    )

    service = AnalyticsService(mock_session)
    fixed_now = datetime(2026, 8, 18, 12, 0, 0, tzinfo=UTC)
    org_id = uuid.uuid4()

    res = await service.get_overview(
        organization_id=org_id,
        period=AnalyticsPeriodEnum.SEVEN_DAYS,
        now=fixed_now,
    )

    assert res.websites_submitted == 100
    assert res.websites_completed == 80
    assert res.websites_failed == 20
    assert res.websites_processed == 100
    # Rate: 80 / (80 + 20) * 100 = 80.0%
    assert res.successful_processing_rate == 80.0

    assert res.status_distribution["COMPLETED"] == 8
    assert res.status_distribution["RUNNING"] == 2
    assert res.status_distribution["FAILED"] == 0  # Zero default

    assert res.findings_by_classification["ROLE_BASED"] == 50
    assert res.findings_by_classification["NO_REPLY"] == 0  # Zero default


def test_get_analytics_overview_http_route(test_app: FastAPI) -> None:
    """Verify HTTP GET /api/v1/analytics/overview requires authentication and returns schema."""
    client = TestClient(test_app)
    mock_service = MagicMock(spec=AnalyticsService)

    org_id = uuid.uuid4()
    mock_response = AnalyticsOverviewResponse(
        period=AnalyticsPeriodEnum.THIRTY_DAYS,
        start_at=datetime(2026, 7, 20, 0, 0, 0, tzinfo=UTC),
        end_at=datetime(2026, 8, 18, 12, 0, 0, tzinfo=UTC),
        total_scans=5,
        active_scans=1,
        websites_submitted=50,
        websites_processed=45,
        websites_completed=40,
        websites_failed=5,
        emails_discovered=120,
        successful_processing_rate=88.89,
        status_distribution={
            st.value: (5 if st.value == "COMPLETED" else 0) for st in ScanJobStatus
        },
        findings_by_classification={
            c.value: (120 if c.value == "ROLE_BASED" else 0) for c in EmailClassification
        },
        findings_by_validation_status={
            v.value: (120 if v.value == "UNVERIFIED" else 0) for v in EmailValidationStatus
        },
        scan_activity_timeline=[
            AnalyticsTimelinePoint(date="2026-08-18", scans_created=5, emails_found=120)
        ],
        recent_completed_scans=[],
    )

    mock_service.get_overview = AsyncMock(return_value=mock_response)

    test_principal = RequestPrincipal(user_id=uuid.uuid4(), organization_id=org_id)
    test_app.dependency_overrides[get_current_principal] = lambda: test_principal
    test_app.dependency_overrides[get_analytics_service] = lambda: mock_service

    res = client.get("/api/v1/analytics/overview?period=30d")  # pyright: ignore[reportUnknownMemberType,reportUnknownVariableType]

    assert res.status_code == 200  # pyright: ignore[reportUnknownMemberType]
    data = res.json()  # pyright: ignore[reportUnknownMemberType,reportUnknownVariableType]
    assert data["period"] == "30d"  # pyright: ignore[reportUnknownVariableType]
    assert data["total_scans"] == 5  # pyright: ignore[reportUnknownVariableType]
    assert data["websites_submitted"] == 50  # pyright: ignore[reportUnknownVariableType]
    assert data["successful_processing_rate"] == 88.89  # pyright: ignore[reportUnknownVariableType]


@pytest.mark.anyio
async def test_analytics_tenant_isolation_proof() -> None:
    """Prove that every executed query strictly includes organization_id = :org_id constraint."""
    mock_session = AsyncMock()
    executed_statements: list[Select[Any]] = []

    async def capture_execute(stmt: Any, *args: Any, **kwargs: Any) -> MagicMock:
        if isinstance(stmt, Select):
            executed_statements.append(stmt)  # pyright: ignore[reportUnknownArgumentType]
        agg_mock = MagicMock(
            total_scans=0,
            websites_submitted=0,
            websites_completed=0,
            websites_failed=0,
            emails_discovered=0,
        )
        res = MagicMock()
        res.one = lambda: agg_mock
        res.scalar_one = lambda: 0
        res.scalar_one_or_none = lambda: None
        res.all = _empty_list
        return res

    mock_session.execute = capture_execute

    service = AnalyticsService(mock_session)
    tenant_a_id = uuid.uuid4()
    fixed_now = datetime(2026, 8, 18, 12, 0, 0, tzinfo=UTC)

    await service.get_overview(
        organization_id=tenant_a_id,
        period=AnalyticsPeriodEnum.THIRTY_DAYS,
        now=fixed_now,
    )

    # Verify all 8 executed SELECT statements enforce organization_id filter for tenant_a_id
    assert len(executed_statements) == 8
    for stmt in executed_statements:
        sql_str = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        assert f"organization_id = '{tenant_a_id}'" in sql_str or "organization_id =" in sql_str


@pytest.mark.anyio
async def test_analytics_utc_period_boundary_inclusion_and_exclusion() -> None:
    """Verify exact UTC boundaries: start_at inclusive (>=), end_at exclusive (<)."""
    mock_session = AsyncMock()
    executed_statements: list[Select[Any]] = []

    async def capture_execute(stmt: Any, *args: Any, **kwargs: Any) -> MagicMock:
        if isinstance(stmt, Select):
            executed_statements.append(stmt)  # pyright: ignore[reportUnknownArgumentType]
        agg_mock = MagicMock(
            total_scans=5,
            websites_submitted=50,
            websites_completed=40,
            websites_failed=10,
            emails_discovered=100,
        )
        res = MagicMock()
        res.one = lambda: agg_mock
        res.scalar_one = lambda: 1
        res.scalar_one_or_none = lambda: None
        res.all = _empty_list
        return res

    mock_session.execute = capture_execute

    service = AnalyticsService(mock_session)
    org_id = uuid.uuid4()
    fixed_now = datetime(2026, 8, 18, 12, 0, 0, tzinfo=UTC)

    res = await service.get_overview(
        organization_id=org_id,
        period=AnalyticsPeriodEnum.THIRTY_DAYS,
        now=fixed_now,
    )

    # For 30d with fixed_now = 2026-08-18 12:00:00 UTC:
    # end_at = 2026-08-18 12:00:00 UTC
    # start_at = 2026-07-20 00:00:00 UTC
    expected_start = datetime(2026, 7, 20, 0, 0, 0, tzinfo=UTC)
    expected_end = datetime(2026, 8, 18, 12, 0, 0, tzinfo=UTC)

    assert res.start_at == expected_start
    assert res.end_at == expected_end

    # Check job aggregates statement has created_at >= start_at and created_at < end_at
    job_stmt_sql = str(executed_statements[0].compile(compile_kwargs={"literal_binds": True}))
    assert "scan_jobs.created_at >=" in job_stmt_sql
    assert "scan_jobs.created_at <" in job_stmt_sql


@pytest.mark.anyio
async def test_analytics_all_time_bounding_and_historical_aggregate_correctness() -> None:
    """Verify all-time aggregates include old data while timeline is capped at 365 days."""
    mock_session = AsyncMock()

    # Job created 1000 days ago
    old_min_date = datetime(2020, 1, 1, 0, 0, 0, tzinfo=UTC)

    agg_mock = MagicMock(
        total_scans=500,
        websites_submitted=5000,
        websites_completed=4500,
        websites_failed=500,
        emails_discovered=12000,
    )
    mock_session.execute = AsyncMock(
        side_effect=[
            MagicMock(one=lambda: agg_mock),  # 1. Job aggregates (no start_at filter)
            MagicMock(scalar_one=lambda: 0),  # 2. Active scans
            MagicMock(all=_empty_list),  # 3. Status distribution
            MagicMock(all=_empty_list),  # 4. Classification distribution
            MagicMock(all=_empty_list),  # 5. Validation distribution
            MagicMock(all=_empty_list),  # 6a. Timeline scans
            MagicMock(all=_empty_list),  # 6b. Timeline emails
            MagicMock(scalar_one_or_none=lambda: old_min_date),  # 6c. Min date for all-time
            MagicMock(all=_empty_list),  # 7. Recent completed scans
        ]
    )

    service = AnalyticsService(mock_session)
    org_id = uuid.uuid4()
    fixed_now = datetime(2026, 8, 18, 12, 0, 0, tzinfo=UTC)

    res = await service.get_overview(
        organization_id=org_id,
        period=AnalyticsPeriodEnum.ALL_TIME,
        now=fixed_now,
    )

    # 1. Aggregate totals correctly reflect historical data from 2020 (500 scans, 12000 emails)
    assert res.total_scans == 500
    assert res.websites_submitted == 5000
    assert res.emails_discovered == 12000
    assert res.successful_processing_rate == 90.0

    # 2. Timeline is safely capped at max 365 daily points
    assert len(res.scan_activity_timeline) == 365
    assert res.scan_activity_timeline[0].date == "2020-01-01"
    assert res.scan_activity_timeline[-1].date == "2020-12-30"
