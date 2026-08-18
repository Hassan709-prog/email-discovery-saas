"""Service-level unit tests for AuthService operations, transactions, and security guarantees."""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from email_discovery_api.config import Settings
from email_discovery_api.models import (
    Membership,
    MembershipRole,
    MembershipStatus,
    Organization,
    OrganizationStatus,
    RefreshSession,
    RefreshSessionStatus,
    User,
    UserStatus,
)
from email_discovery_api.schemas.auth import LoginRequest, RegisterRequest
from email_discovery_api.services.auth import (
    AuthService,
    AuthServiceError,
    AuthSuccessResult,
    OrganizationSelectionRequiredResult,
    ServiceErrorCode,
)
from email_discovery_api.services.passwords import PasswordService
from email_discovery_api.services.tokens import TokenService


@pytest.fixture
def fast_password_service() -> PasswordService:
    hasher = PasswordHash((Argon2Hasher(memory_cost=1024, time_cost=1, parallelism=1),))
    return PasswordService(hasher=hasher)


@pytest.mark.anyio
async def test_register_creates_user_org_membership_session_in_one_transaction(
    test_settings: Settings, fast_password_service: PasswordService
) -> None:
    """Verify registration creates User, Org, Membership, and Session in 1 transaction."""
    mock_session = AsyncMock(spec=AsyncSession)
    mock_session.commit = AsyncMock()

    service = AuthService(
        session=mock_session,
        settings=test_settings,
        password_service=fast_password_service,
    )

    req = RegisterRequest(
        email="owner@example.com",
        password="SecurePassword123!",
        display_name="Org Owner",
        organization_name="Acme Corp",
        organization_slug="acme-corp",
    )

    result = await service.register(req)

    assert isinstance(result, AuthSuccessResult)
    assert result.response.access_token is not None
    assert result.raw_refresh_token is not None
    assert result.raw_csrf_token is not None
    mock_session.commit.assert_awaited_once()


@pytest.mark.anyio
async def test_register_without_organization_name_uses_display_name_and_random_uuid_slug(
    test_settings: Settings, fast_password_service: PasswordService
) -> None:
    """Verify registration without org name auto-creates personal workspace with random slug."""
    mock_session = AsyncMock(spec=AsyncSession)
    mock_session.commit = AsyncMock()

    service = AuthService(
        session=mock_session,
        settings=test_settings,
        password_service=fast_password_service,
    )

    mock_user = MagicMock(id=uuid.uuid4(), auth_version=1)
    mock_org = MagicMock(id=uuid.uuid4())

    service._user_repo.create_user = AsyncMock(return_value=mock_user)  # pyright: ignore[reportPrivateUsage]
    service._user_repo.create_organization = AsyncMock(return_value=mock_org)  # pyright: ignore[reportPrivateUsage]
    service._user_repo.create_membership = AsyncMock()  # pyright: ignore[reportPrivateUsage]
    service._refresh_repo.create_session = AsyncMock()  # pyright: ignore[reportPrivateUsage]

    req = RegisterRequest(
        email="personal@example.com",
        password="SecurePassword123!",
        display_name="Hassan Malik",
    )

    result = await service.register(req)

    assert isinstance(result, AuthSuccessResult)
    mock_session.commit.assert_awaited_once()

    # Check create_organization was called with personal workspace name and random slug
    _args, kwargs = service._user_repo.create_organization.call_args  # pyright: ignore[reportPrivateUsage]
    assert kwargs["name"] == "Hassan Malik's Workspace"
    assert kwargs["slug"].startswith("workspace-")
    assert len(kwargs["slug"]) >= 40  # 'workspace-' + 32 hex chars


@pytest.mark.anyio
async def test_register_uniqueness_conflict_recovers_gracefully(
    test_settings: Settings, fast_password_service: PasswordService
) -> None:
    """Verify IntegrityError during registration raises EMAIL_OR_SLUG_CONFLICT."""
    mock_session = AsyncMock(spec=AsyncSession)
    mock_session.commit = AsyncMock(side_effect=IntegrityError("Duplicate key", None, Exception()))
    mock_session.rollback = AsyncMock()

    service = AuthService(
        session=mock_session,
        settings=test_settings,
        password_service=fast_password_service,
    )

    req = RegisterRequest(
        email="existing@example.com",
        password="SecurePassword123!",
        organization_name="Existing Corp",
    )

    with pytest.raises(AuthServiceError) as exc_info:
        await service.register(req)

    assert exc_info.value.code == ServiceErrorCode.EMAIL_OR_SLUG_CONFLICT
    mock_session.rollback.assert_awaited_once()


@pytest.mark.anyio
async def test_login_dummy_hash_path_for_unknown_user(
    test_settings: Settings, fast_password_service: PasswordService
) -> None:
    """Verify login with unknown email raises INVALID_CREDENTIALS after dummy verification."""
    mock_session = AsyncMock(spec=AsyncSession)
    mock_session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: None))

    service = AuthService(
        session=mock_session,
        settings=test_settings,
        password_service=fast_password_service,
    )

    req = LoginRequest(email="missing@example.com", password="SomePassword123!")

    with pytest.raises(AuthServiceError) as exc_info:
        await service.login(req)

    assert exc_info.value.code == ServiceErrorCode.INVALID_CREDENTIALS


@pytest.mark.anyio
async def test_login_multi_tenant_requires_organization_selection(
    test_settings: Settings, fast_password_service: PasswordService
) -> None:
    """Verify multi-tenant user login requires organization selection when org is None."""
    mock_session = AsyncMock(spec=AsyncSession)
    pwd_hash = await fast_password_service.hash_password("ValidPassword123!")

    user_id = uuid.uuid4()
    org1_id = uuid.uuid4()
    org2_id = uuid.uuid4()

    user = User(
        id=user_id,
        email="multi@example.com",
        normalized_email="multi@example.com",
        password_hash=pwd_hash,
        status=UserStatus.ACTIVE.value,
        auth_version=1,
    )
    m1 = Membership(
        id=uuid.uuid4(),
        user_id=user_id,
        organization_id=org1_id,
        role=MembershipRole.OWNER.value,
        status=MembershipStatus.ACTIVE.value,
    )
    o1 = Organization(
        id=org1_id, name="Org 1", slug="org-1", status=OrganizationStatus.ACTIVE.value
    )
    m2 = Membership(
        id=uuid.uuid4(),
        user_id=user_id,
        organization_id=org2_id,
        role=MembershipRole.MEMBER.value,
        status=MembershipStatus.ACTIVE.value,
    )
    o2 = Organization(
        id=org2_id, name="Org 2", slug="org-2", status=OrganizationStatus.ACTIVE.value
    )

    # Setup mocks
    mock_session.execute = AsyncMock(
        side_effect=[
            MagicMock(scalar_one_or_none=lambda: user),
            MagicMock(tuples=lambda: MagicMock(all=lambda: [(m1, o1), (m2, o2)])),
        ]
    )

    service = AuthService(
        session=mock_session,
        settings=test_settings,
        password_service=fast_password_service,
    )

    req = LoginRequest(email="multi@example.com", password="ValidPassword123!")
    result = await service.login(req)

    assert isinstance(result, OrganizationSelectionRequiredResult)
    assert len(result.response.organizations) == 2


@pytest.mark.anyio
async def test_rotated_refresh_token_reuse_compromises_family_and_commits(
    test_settings: Settings,
) -> None:
    """Requirement 2 Test: Reusing a ROTATED token marks family COMPROMISED and commits."""
    mock_session = AsyncMock(spec=AsyncSession)
    mock_session.commit = AsyncMock()

    token_service = TokenService(test_settings)
    raw_token, raw_csrf, token_hash, csrf_hash, expires_at = (
        token_service.generate_refresh_credentials()
    )

    family_id = uuid.uuid4()
    user_id = uuid.uuid4()
    org_id = uuid.uuid4()

    rotated_session = RefreshSession(
        id=uuid.uuid4(),
        user_id=user_id,
        organization_id=org_id,
        family_id=family_id,
        token_hash=token_hash,
        csrf_token_hash=csrf_hash,
        status=RefreshSessionStatus.ROTATED.value,
        auth_version=1,
        expires_at=expires_at,
    )

    mock_session.execute = AsyncMock(
        side_effect=[
            MagicMock(scalar_one_or_none=lambda: rotated_session),
            MagicMock(rowcount=1),  # revoke_family update rowcount
        ]
    )

    service = AuthService(
        session=mock_session,
        settings=test_settings,
        token_service=token_service,
    )

    with pytest.raises(AuthServiceError) as exc_info:
        await service.refresh(raw_token, raw_csrf)

    assert exc_info.value.code == ServiceErrorCode.REFRESH_REUSE_DETECTED
    # CRITICAL: Verify commit was awaited so family compromise persists in DB!
    mock_session.commit.assert_awaited_once()
