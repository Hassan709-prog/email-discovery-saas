"""Reusable PostgreSQL test database support fixtures and safety guards."""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncGenerator
from typing import Any

import pytest
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from email_discovery_api.config import Settings
from email_discovery_api.models import Base, Membership, Organization, User
from email_discovery_api.models.enums import (
    MembershipRole,
    MembershipStatus,
    OrganizationStatus,
    UserStatus,
)
from email_discovery_api.models.helpers import normalize_email
from email_discovery_api.services.passwords import PasswordService
from email_discovery_api.services.tokens import TokenService

DEFAULT_TEST_PG_URL = "postgresql+asyncpg://postgres:postgres@localhost:5432/email_discovery_test"


def validate_test_database_url(url: str) -> str:
    """Validate that target test database URL points strictly to 'email_discovery_test'."""
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


@pytest.fixture
def test_settings() -> Settings:
    """Fixture providing isolated Settings for test execution."""
    return Settings(
        app_name="test-api",
        environment="development",
        database_url=SecretStr(DEFAULT_TEST_PG_URL),
        jwt_secret_key=SecretStr("test-secret-key-min-32-chars-long-for-testing-purposes"),
        db_health_timeout_seconds=1.0,
    )


@pytest.fixture
async def isolated_db_engine() -> AsyncGenerator[AsyncEngine]:
    """Create isolated PostgreSQL engine targeting dedicated email_discovery_test database."""
    test_db_url = os.getenv("TEST_DATABASE_URL", DEFAULT_TEST_PG_URL)

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

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as conn_err:
        await engine.dispose()
        pytest.skip(
            f"Isolated PostgreSQL test database 'email_discovery_test' unavailable ({conn_err})."
        )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
    except Exception:
        pass
    finally:
        await engine.dispose()


@pytest.fixture
async def test_user_and_token(
    isolated_db_engine: AsyncEngine, test_settings: Settings
) -> dict[str, Any]:
    """Seed isolated database with user/org, returning a valid Bearer token."""
    session_factory = async_sessionmaker(
        bind=isolated_db_engine,
        expire_on_commit=False,
        class_=AsyncSession,
    )

    pwd_service = PasswordService(test_settings)
    token_service = TokenService(test_settings)

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
        "session_factory": session_factory,
    }
