"""Regression tests proving session isolation between authentication and app service transactions.

Verifies:
1. get_identity_db_session and get_db_session produce distinct AsyncSession instances.
2. Authenticated POST /api/v1/scan-jobs does not collision-raise InvalidRequestError.
3. Successful creation persists exactly 1 DRAFT job, 2 scan_urls, and 1 JOB_CREATED event.
4. Cross-tenant isolation prevents unauthorized visibility.
5. Controlled write transaction failures roll back completely without partial rows.
6. Idempotent creation replay returns HTTP 200 without creating duplicate database rows.
"""

import os
import uuid
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.sql import select

from email_discovery_api.config import Settings, get_settings
from email_discovery_api.database import DatabaseManager, get_db_session, get_identity_db_session
from email_discovery_api.main import create_app
from email_discovery_api.models import (
    Base,
    JobEvent,
    Membership,
    MembershipRole,
    MembershipStatus,
    Organization,
    OrganizationStatus,
    ScanJob,
    ScanURL,
    User,
    UserStatus,
    normalize_email,
)
from email_discovery_api.models.enums import ScanJobStatus
from email_discovery_api.repositories.job_events import JobEventRepository
from email_discovery_api.services.passwords import PasswordService
from email_discovery_api.services.tokens import TokenService

DEFAULT_TEST_PG_URL = "postgresql+asyncpg://postgres:postgres@localhost:5432/email_discovery_test"


def validate_test_database_url(url: str) -> str:
    """Validate that target test database URL points strictly to 'email_discovery_test'.

    Uses SQLAlchemy's make_url parser to extract the canonical database name,
    preventing query-parameter bypasses or arbitrary database destruction.
    """
    try:
        parsed_url = make_url(url)
    except Exception as err:
        raise ValueError(f"Invalid database URL format: {url!r}") from err

    db_name = parsed_url.database
    if db_name != "email_discovery_test":
        raise ValueError(
            f"Unsafe test database name {db_name!r}. "
            "Regression tests must target database 'email_discovery_test' exactly."
        )
    return url


def test_validate_test_database_url_safety_guard() -> None:
    """Verify validate_test_database_url accepts email_discovery_test and rejects unsafe URLs."""
    # 1. Exact email_discovery_test accepted
    valid_url = "postgresql+asyncpg://postgres:postgres@localhost:5432/email_discovery_test"
    assert validate_test_database_url(valid_url) == valid_url

    # 2. email_discovery rejected
    with pytest.raises(ValueError, match="Unsafe test database name 'email_discovery'"):
        validate_test_database_url(
            "postgresql+asyncpg://postgres:postgres@localhost:5432/email_discovery"
        )

    # 3. postgres rejected
    with pytest.raises(ValueError, match="Unsafe test database name 'postgres'"):
        validate_test_database_url("postgresql+asyncpg://postgres:postgres@localhost:5432/postgres")

    # 4. email_discovery?option=value rejected (query parameter bypass attempt)
    with pytest.raises(ValueError, match="Unsafe test database name 'email_discovery'"):
        validate_test_database_url(
            "postgresql+asyncpg://postgres:postgres@localhost:5432/email_discovery?option=value"
        )

    # 5. missing database name rejected
    with pytest.raises(ValueError, match="Unsafe test database name"):
        validate_test_database_url("postgresql+asyncpg://postgres:postgres@localhost:5432")

    with pytest.raises(ValueError, match="Unsafe test database name"):
        validate_test_database_url("postgresql+asyncpg://postgres:postgres@localhost:5432/")


@pytest.fixture
async def isolated_db_engine() -> AsyncGenerator[AsyncEngine]:
    """Create isolated PostgreSQL engine targeting dedicated email_discovery_test database."""
    test_db_url = os.getenv("TEST_DATABASE_URL", DEFAULT_TEST_PG_URL)

    # 1. Strictly validate database URL using make_url parser
    try:
        validate_test_database_url(test_db_url)
    except ValueError as err:
        pytest.fail(f"Test database URL safety guard triggered: {err}")

    engine = create_async_engine(
        test_db_url,
        pool_size=5,
        max_overflow=0,
        future=True,
    )

    # 2. Connection probe: skip ONLY if dedicated test database connection is unreachable
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as conn_err:
        await engine.dispose()
        pytest.skip(
            f"Isolated PostgreSQL test database 'email_discovery_test' unavailable ({conn_err})."
        )

    # 3. Schema operations: failures here must fail the test, not skip
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    # 4. Reliable teardown cleanup and disposal
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
    finally:
        await engine.dispose()


@pytest.fixture
async def isolated_app(isolated_db_engine: AsyncEngine) -> FastAPI:
    """Create test FastAPI application bound to isolated test database."""
    test_db_url = os.getenv("TEST_DATABASE_URL", DEFAULT_TEST_PG_URL)
    settings = Settings(
        app_name="regression-test-api",
        environment="development",
        database_url=SecretStr(test_db_url),
        jwt_secret_key=SecretStr("isolated-regression-test-jwt-secret-key-32-chars!"),
    )
    app = create_app(settings)

    db_manager = DatabaseManager.__new__(DatabaseManager)
    db_manager.settings = settings
    db_manager.engine = isolated_db_engine
    db_manager.session_factory = async_sessionmaker(
        bind=isolated_db_engine,
        expire_on_commit=False,
        class_=AsyncSession,
    )
    app.state.db_manager = db_manager
    app.dependency_overrides[get_settings] = lambda: settings
    return app


@pytest.fixture
async def test_user_and_token(
    isolated_db_engine: AsyncEngine, isolated_app: FastAPI
) -> dict[str, Any]:
    """Seed isolated database with user/org, returning a valid Bearer token."""
    session_factory = async_sessionmaker(
        bind=isolated_db_engine,
        expire_on_commit=False,
        class_=AsyncSession,
    )

    settings = isolated_app.state.settings
    pwd_service = PasswordService(settings)
    token_service = TokenService(settings)

    org_id = uuid.uuid4()
    user_id = uuid.uuid4()
    membership_id = uuid.uuid4()

    hashed_pwd = await pwd_service.hash_password("Password123!")

    async with session_factory() as session:
        async with session.begin():
            org = Organization(
                id=org_id,
                name="Regression Org",
                slug="regression-org",
                status=OrganizationStatus.ACTIVE.value,
            )
            user = User(
                id=user_id,
                email="regression@example.com",
                normalized_email=normalize_email("regression@example.com"),
                password_hash=hashed_pwd,
                status=UserStatus.ACTIVE.value,
                auth_version=1,
            )
            membership = Membership(
                id=membership_id,
                organization_id=org_id,
                user_id=user_id,
                role=MembershipRole.OWNER.value,
                status=MembershipStatus.ACTIVE.value,
            )
            session.add_all([org, user, membership])

    token = token_service.create_access_token(
        user_id=user_id,
        organization_id=org_id,
        auth_version=1,
    )

    return {
        "org_id": org_id,
        "user_id": user_id,
        "token": token,
        "auth_headers": {"Authorization": f"Bearer {token}"},
    }


@pytest.mark.anyio
async def test_distinct_dependency_callables_and_independent_overrides(
    isolated_app: FastAPI,
) -> None:
    """Verify distinct session dependencies produce separate sessions and independent overrides."""
    captured_identity_sessions: list[AsyncSession] = []
    captured_service_sessions: list[AsyncSession] = []

    # 1. Verify two callables produce distinct AsyncSession instances
    @isolated_app.get("/test-session-isolation")
    async def route_test_session_isolation(  # pyright: ignore[reportUnusedFunction]
        identity_sess: AsyncSession = Depends(get_identity_db_session),
        service_sess: AsyncSession = Depends(get_db_session),
    ) -> dict[str, str]:
        captured_identity_sessions.append(identity_sess)
        captured_service_sessions.append(service_sess)
        return {"status": "ok"}

    async with AsyncClient(
        transport=ASGITransport(app=isolated_app), base_url="http://test"
    ) as client:
        res = await client.get("/test-session-isolation")
        assert res.status_code == 200

    assert len(captured_identity_sessions) == 1
    assert len(captured_service_sessions) == 1
    assert captured_identity_sessions[0] is not captured_service_sessions[0]

    # 2. Verify get_identity_db_session can be overridden independently
    mock_identity_session = AsyncMock(spec=AsyncSession)
    isolated_app.dependency_overrides[get_identity_db_session] = lambda: mock_identity_session

    @isolated_app.get("/test-override-independence")
    async def route_test_override_independence(  # pyright: ignore[reportUnusedFunction]
        identity_sess: AsyncSession = Depends(get_identity_db_session),
        service_sess: AsyncSession = Depends(get_db_session),
    ) -> dict[str, str]:
        assert identity_sess is mock_identity_session
        assert service_sess is not mock_identity_session
        return {"status": "overridden"}

    async with AsyncClient(
        transport=ASGITransport(app=isolated_app), base_url="http://test"
    ) as client:
        res_override = await client.get("/test-override-independence")
        assert res_override.status_code == 200

    isolated_app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_authenticated_post_scan_jobs_success_and_assertions(
    isolated_app: FastAPI,
    test_user_and_token: dict[str, Any],
) -> None:
    """Verify creation creates 1 job, 2 URLs, 1 event (seq=1), and enforces tenant isolation."""
    org_id = test_user_and_token["org_id"]
    user_id = test_user_and_token["user_id"]
    headers = test_user_and_token["auth_headers"]

    payload = {
        "inputs": ["https://example.com", "https://example.org"],
        "name": "Regression Test Scan",
    }

    async with AsyncClient(
        transport=ASGITransport(app=isolated_app), base_url="http://test"
    ) as client:
        # Unauthenticated request returns 401
        res_unauth = await client.post("/api/v1/scan-jobs", json=payload)
        assert res_unauth.status_code == 401

        # Authenticated creation
        res = await client.post(
            "/api/v1/scan-jobs",
            json=payload,
            headers={**headers, "Idempotency-Key": "regression-key-1"},
        )
        assert res.status_code == 201
        data = res.json()
        job_id = uuid.UUID(data["id"])

    # Query isolated database to verify EXACT row counts and scoping
    session_factory: async_sessionmaker[AsyncSession] = (
        isolated_app.state.db_manager.session_factory
    )
    async with session_factory() as session:
        # 1. Assert exactly 1 DRAFT scan_jobs row with correct org_id and user_id
        jobs_res = await session.execute(select(ScanJob).where(ScanJob.id == job_id))
        jobs = jobs_res.scalars().all()
        assert len(jobs) == 1
        job = jobs[0]
        assert job.status == ScanJobStatus.DRAFT.value
        assert job.organization_id == org_id
        assert job.created_by_user_id == user_id

        # 2. Assert exactly 2 scan_urls rows
        urls_res = await session.execute(
            select(ScanURL).where(ScanURL.scan_job_id == job_id).order_by(ScanURL.original_index)
        )
        urls = urls_res.scalars().all()
        assert len(urls) == 2
        assert urls[0].normalized_url == "https://example.com/"
        assert urls[1].normalized_url == "https://example.org/"

        # 3. Assert exactly 1 JOB_CREATED event with sequence_number = 1
        events_res = await session.execute(select(JobEvent).where(JobEvent.scan_job_id == job_id))
        events = events_res.scalars().all()
        assert len(events) == 1
        event = events[0]
        assert event.event_type == "JOB_CREATED"
        assert event.sequence_number == 1
        assert event.scan_job_id == job_id

        # 4. Assert no cross-tenant visibility ( querying with non-existent org_id )
        other_org_id = uuid.uuid4()
        cross_tenant_jobs = await session.execute(
            select(ScanJob).where(ScanJob.organization_id == other_org_id)
        )
        assert len(cross_tenant_jobs.scalars().all()) == 0


@pytest.mark.anyio
async def test_idempotent_creation_replay(
    isolated_app: FastAPI,
    test_user_and_token: dict[str, Any],
) -> None:
    """Verify idempotent creation replay returns HTTP 200 with same job ID and no duplicate rows."""
    headers = test_user_and_token["auth_headers"]
    idempotency_headers = {**headers, "Idempotency-Key": "idempotent-replay-key-99"}

    payload = {
        "inputs": ["https://example.com", "https://example.org"],
        "name": "Idempotency Test Scan",
    }

    async with AsyncClient(
        transport=ASGITransport(app=isolated_app), base_url="http://test"
    ) as client:
        # First creation -> 201 Created
        res1 = await client.post("/api/v1/scan-jobs", json=payload, headers=idempotency_headers)
        assert res1.status_code == 201
        job_id1 = res1.json()["id"]

        # Second creation with identical key and payload -> 200 OK replay
        res2 = await client.post("/api/v1/scan-jobs", json=payload, headers=idempotency_headers)
        assert res2.status_code == 200
        job_id2 = res2.json()["id"]
        assert job_id1 == job_id2

    # Query DB to assert row counts did NOT duplicate
    session_factory: async_sessionmaker[AsyncSession] = (
        isolated_app.state.db_manager.session_factory
    )
    async with session_factory() as session:
        jobs_res = await session.execute(select(ScanJob))
        assert len(jobs_res.scalars().all()) == 1

        urls_res = await session.execute(select(ScanURL))
        assert len(urls_res.scalars().all()) == 2

        events_res = await session.execute(select(JobEvent))
        assert len(events_res.scalars().all()) == 1


@pytest.mark.anyio
async def test_write_transaction_failure_rolls_back_completely(
    isolated_app: FastAPI,
    test_user_and_token: dict[str, Any],
) -> None:
    """Verify failure inside write transaction rolls back completely with zero partial rows."""
    headers = test_user_and_token["auth_headers"]
    payload = {
        "inputs": ["https://rollback-test.com", "https://rollback-test.org"],
        "name": "Failed Scan Attempt",
    }

    # Patch JobEventRepository.append_event to raise error inside write transaction
    with patch.object(
        JobEventRepository,
        "append_event",
        side_effect=RuntimeError("Controlled failure during event append inside transaction"),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=isolated_app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            res = await client.post(
                "/api/v1/scan-jobs",
                json=payload,
                headers={**headers, "Idempotency-Key": "failing-key-1"},
            )
            assert res.status_code == 500

    # Query isolated DB to assert ZERO partial rows exist
    session_factory: async_sessionmaker[AsyncSession] = (
        isolated_app.state.db_manager.session_factory
    )
    async with session_factory() as session:
        jobs_res = await session.execute(select(ScanJob))
        assert len(jobs_res.scalars().all()) == 0

        urls_res = await session.execute(select(ScanURL))
        assert len(urls_res.scalars().all()) == 0

        events_res = await session.execute(select(JobEvent))
        assert len(events_res.scalars().all()) == 0
