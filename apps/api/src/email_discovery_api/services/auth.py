"""Auth service owning transactions, verification, and refresh session lifecycle."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from email_discovery_api.config import Settings, get_settings
from email_discovery_api.models import (
    AuditLog,
    MembershipRole,
    MembershipStatus,
    OrganizationStatus,
    RefreshSessionStatus,
    UserStatus,
    normalize_email,
    normalize_org_slug,
)
from email_discovery_api.repositories.refresh_sessions import RefreshSessionRepository
from email_discovery_api.repositories.users import UserRepository
from email_discovery_api.schemas.auth import (
    AuthSuccessResponse,
    LoginRequest,
    OrganizationChoiceSchema,
    OrganizationSelectionRequiredResponse,
    RegisterRequest,
    UserProfileResponse,
)
from email_discovery_api.services.passwords import PasswordPolicyError, PasswordService
from email_discovery_api.services.rate_limiter import AuthAttemptLimiter, InMemoryAuthAttemptLimiter
from email_discovery_api.services.tokens import TokenService, hash_token, verify_csrf_token

logger = logging.getLogger("email_discovery_api.services.auth")


class ServiceErrorCode:
    """Stable domain error codes for authentication service errors."""

    INVALID_CREDENTIALS = "INVALID_CREDENTIALS"
    INVALID_TOKEN = "INVALID_TOKEN"
    ORGANIZATION_SELECTION_REQUIRED = "ORGANIZATION_SELECTION_REQUIRED"
    EMAIL_OR_SLUG_CONFLICT = "EMAIL_OR_SLUG_CONFLICT"
    PASSWORD_POLICY_VIOLATION = "PASSWORD_POLICY_VIOLATION"
    REFRESH_REUSE_DETECTED = "REFRESH_REUSE_DETECTED"
    CSRF_VALIDATION_FAILED = "CSRF_VALIDATION_FAILED"
    RATE_LIMITED = "RATE_LIMITED"


class AuthServiceError(Exception):
    """Domain exception raised by AuthService operations."""

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


@dataclass(frozen=True)
class AuthSuccessResult:
    """Result container for successful login or token refresh."""

    response: AuthSuccessResponse
    raw_refresh_token: str
    raw_csrf_token: str


@dataclass(frozen=True)
class OrganizationSelectionRequiredResult:
    """Result container when user has multiple organizations requiring selection."""

    response: OrganizationSelectionRequiredResponse


_GLOBAL_RATE_LIMITER = InMemoryAuthAttemptLimiter()


class AuthService:
    """Application service for user authentication and refresh session management."""

    def __init__(
        self,
        session: AsyncSession,
        settings: Settings | None = None,
        password_service: PasswordService | None = None,
        token_service: TokenService | None = None,
        rate_limiter: AuthAttemptLimiter | None = None,
    ) -> None:
        self._session = session
        self._settings = settings or get_settings()
        self._password_service = password_service or PasswordService(self._settings)
        self._token_service = token_service or TokenService(self._settings)
        self._rate_limiter = rate_limiter or _GLOBAL_RATE_LIMITER
        self._user_repo = UserRepository(session)
        self._refresh_repo = RefreshSessionRepository(session)

    async def register(
        self, command: RegisterRequest, request_id: str = "system"
    ) -> AuthSuccessResult:
        """Self-register a new user, organization, owner membership, and initial refresh session."""
        try:
            self._password_service.validate_password_policy(command.password)
        except PasswordPolicyError as exc:
            raise AuthServiceError(ServiceErrorCode.PASSWORD_POLICY_VIOLATION, str(exc)) from exc

        norm_email = normalize_email(str(command.email))
        raw_slug = command.organization_slug or command.organization_name
        norm_slug = normalize_org_slug(raw_slug)

        # Hash password before database transaction
        pwd_hash = await self._password_service.hash_password(command.password)

        try:
            # 1. Create User
            user = await self._user_repo.create_user(
                email=str(command.email),
                normalized_email=norm_email,
                password_hash=pwd_hash,
                display_name=command.display_name,
                status=UserStatus.ACTIVE.value,
            )

            # 2. Create Organization
            org = await self._user_repo.create_organization(
                name=command.organization_name,
                slug=norm_slug,
                status=OrganizationStatus.ACTIVE.value,
            )

            # 3. Create OWNER Membership
            await self._user_repo.create_membership(
                user_id=user.id,
                organization_id=org.id,
                role=MembershipRole.OWNER.value,
                status=MembershipStatus.ACTIVE.value,
            )

            # 4. Generate Refresh & CSRF credentials
            now = datetime.now(UTC)
            (
                raw_refresh,
                raw_csrf,
                refresh_hash,
                csrf_hash,
                expires_at,
            ) = self._token_service.generate_refresh_credentials(now=now)

            family_id = uuid.uuid4()
            await self._refresh_repo.create_session(
                user_id=user.id,
                organization_id=org.id,
                family_id=family_id,
                token_hash=refresh_hash,
                csrf_token_hash=csrf_hash,
                auth_version=user.auth_version,
                expires_at=expires_at,
            )

            # 5. Create AuditLog (sanitized without credentials)
            audit = AuditLog(
                organization_id=org.id,
                actor_user_id=user.id,
                action="AUTH_REGISTER",
                target_type="users",
                target_id=str(user.id),
                metadata_={"email": norm_email, "org_slug": norm_slug},
            )
            self._session.add(audit)

            # 6. Commit transaction atomically
            await self._session.commit()

        except IntegrityError as exc:
            await self._session.rollback()
            raise AuthServiceError(
                ServiceErrorCode.EMAIL_OR_SLUG_CONFLICT,
                "User with this email or organization slug already exists.",
            ) from exc

        # Create JWT access token
        access_token = self._token_service.create_access_token(
            user_id=user.id,
            organization_id=org.id,
            auth_version=user.auth_version,
            now=now,
        )

        response = AuthSuccessResponse(
            access_token=access_token,
            expires_in_seconds=self._settings.access_token_ttl_minutes * 60,
        )
        return AuthSuccessResult(
            response=response,
            raw_refresh_token=raw_refresh,
            raw_csrf_token=raw_csrf,
        )

    async def login(
        self, command: LoginRequest, request_id: str = "system"
    ) -> AuthSuccessResult | OrganizationSelectionRequiredResult:
        """Authenticate user by email and password, selecting or requesting an organization."""
        norm_email = normalize_email(str(command.email))
        rate_key = f"login:{norm_email}"
        if not self._rate_limiter.check_rate_limit(rate_key):
            raise AuthServiceError(
                ServiceErrorCode.RATE_LIMITED,
                "Too many authentication attempts. Please try again later.",
            )
        self._rate_limiter.record_attempt(rate_key)

        user = await self._user_repo.get_by_email(norm_email)

        # Verification & Constant-Time Dummy Path for missing user / password_hash
        if user is None or not user.password_hash or user.status != UserStatus.ACTIVE.value:
            await self._password_service.verify_password(command.password, None)
            raise AuthServiceError(
                ServiceErrorCode.INVALID_CREDENTIALS,
                "Invalid email address or password.",
            )

        # Verify plain password
        valid, new_hash = await self._password_service.verify_and_rehash(
            command.password, user.password_hash
        )
        if not valid:
            raise AuthServiceError(
                ServiceErrorCode.INVALID_CREDENTIALS,
                "Invalid email address or password.",
            )

        # Rehash if password policy updated
        if new_hash:
            user.password_hash = new_hash

        # Fetch active memberships and active organizations
        memberships = await self._user_repo.get_user_active_memberships(user.id)
        if not memberships:
            raise AuthServiceError(
                ServiceErrorCode.INVALID_CREDENTIALS,
                "Account has no active organization memberships.",
            )

        selected_membership = None
        selected_org = None

        if len(memberships) == 1:
            selected_membership, selected_org = memberships[0]
        else:
            if command.organization_id is None:
                # Multiple memberships require explicit organization selection
                choices = [
                    OrganizationChoiceSchema(
                        id=org.id,
                        name=org.name,
                        slug=org.slug,
                        role=m.role,
                    )
                    for m, org in memberships
                ]
                return OrganizationSelectionRequiredResult(
                    response=OrganizationSelectionRequiredResponse(
                        organization_selection_required=True,
                        organizations=choices,
                    )
                )

            # Validate requested organization against user's active memberships
            for m, org in memberships:
                if org.id == command.organization_id:
                    selected_membership = m
                    selected_org = org
                    break

            if selected_membership is None or selected_org is None:
                raise AuthServiceError(
                    ServiceErrorCode.INVALID_CREDENTIALS,
                    "Invalid organization selected for user account.",
                )

        now = datetime.now(UTC)
        await self._user_repo.update_last_login(user.id, now)

        # Generate Refresh & CSRF credentials
        (
            raw_refresh,
            raw_csrf,
            refresh_hash,
            csrf_hash,
            expires_at,
        ) = self._token_service.generate_refresh_credentials(now=now)

        family_id = uuid.uuid4()
        await self._refresh_repo.create_session(
            user_id=user.id,
            organization_id=selected_org.id,
            family_id=family_id,
            token_hash=refresh_hash,
            csrf_token_hash=csrf_hash,
            auth_version=user.auth_version,
            expires_at=expires_at,
        )

        audit = AuditLog(
            organization_id=selected_org.id,
            actor_user_id=user.id,
            action="AUTH_LOGIN",
            target_type="users",
            target_id=str(user.id),
            metadata_={"email": norm_email, "role": selected_membership.role},
        )
        self._session.add(audit)
        await self._session.commit()

        access_token = self._token_service.create_access_token(
            user_id=user.id,
            organization_id=selected_org.id,
            auth_version=user.auth_version,
            now=now,
        )

        response = AuthSuccessResponse(
            access_token=access_token,
            expires_in_seconds=self._settings.access_token_ttl_minutes * 60,
        )
        return AuthSuccessResult(
            response=response,
            raw_refresh_token=raw_refresh,
            raw_csrf_token=raw_csrf,
        )

    async def refresh(
        self, raw_refresh_token: str, raw_csrf_header: str | None
    ) -> AuthSuccessResult:
        """Rotate refresh token session, validate CSRF binding, and issue new access token."""
        token_digest = hash_token(raw_refresh_token)
        now = datetime.now(UTC)

        # Lock session row FOR UPDATE
        session_obj = await self._refresh_repo.get_by_token_hash_for_update(token_digest)
        if session_obj is None:
            raise AuthServiceError(
                ServiceErrorCode.INVALID_TOKEN,
                "Authentication failed: invalid or expired session.",
            )

        # Validate CSRF header against stored digest using constant-time comparison
        if not verify_csrf_token(raw_csrf_header, session_obj.csrf_token_hash):
            raise AuthServiceError(
                ServiceErrorCode.CSRF_VALIDATION_FAILED,
                "Invalid CSRF token header.",
            )

        # Check session status
        if session_obj.status == RefreshSessionStatus.ROTATED.value:
            # SECURITY REQUIREMENT 2: Compromise entire token family and COMMIT before returning 401
            await self._refresh_repo.revoke_family(session_obj.family_id, revoked_at=now)
            await self._session.commit()
            raise AuthServiceError(
                ServiceErrorCode.REFRESH_REUSE_DETECTED,
                "Refresh token reuse detected. All sessions in token family revoked.",
            )

        if session_obj.status != RefreshSessionStatus.ACTIVE.value:
            raise AuthServiceError(
                ServiceErrorCode.INVALID_TOKEN,
                "Authentication failed: invalid or expired session.",
            )

        if session_obj.expires_at <= now:
            raise AuthServiceError(
                ServiceErrorCode.INVALID_TOKEN,
                "Authentication failed: invalid or expired session.",
            )

        # Load and validate active user, active org, and active membership
        user = await self._user_repo.get_by_id(session_obj.user_id)
        if user is None or user.status != UserStatus.ACTIVE.value:
            raise AuthServiceError(
                ServiceErrorCode.INVALID_TOKEN,
                "Authentication failed: user account inactive.",
            )

        if session_obj.auth_version != user.auth_version:
            raise AuthServiceError(
                ServiceErrorCode.INVALID_TOKEN,
                "Authentication failed: auth version mismatch.",
            )

        mem_org = await self._user_repo.get_active_membership(
            session_obj.user_id, session_obj.organization_id
        )
        if mem_org is None:
            raise AuthServiceError(
                ServiceErrorCode.INVALID_TOKEN,
                "Authentication failed: tenant membership inactive.",
            )

        # Atomic Rotation in one transaction
        (
            new_raw_refresh,
            new_raw_csrf,
            new_refresh_hash,
            new_csrf_hash,
            new_expires_at,
        ) = self._token_service.generate_refresh_credentials(now=now)

        new_session = await self._refresh_repo.create_session(
            user_id=session_obj.user_id,
            organization_id=session_obj.organization_id,
            family_id=session_obj.family_id,
            token_hash=new_refresh_hash,
            csrf_token_hash=new_csrf_hash,
            auth_version=user.auth_version,
            expires_at=new_expires_at,
        )

        await self._refresh_repo.rotate_session(session_obj, new_session, used_at=now)
        await self._session.commit()

        access_token = self._token_service.create_access_token(
            user_id=user.id,
            organization_id=session_obj.organization_id,
            auth_version=user.auth_version,
            now=now,
        )

        response = AuthSuccessResponse(
            access_token=access_token,
            expires_in_seconds=self._settings.access_token_ttl_minutes * 60,
        )
        return AuthSuccessResult(
            response=response,
            raw_refresh_token=new_raw_refresh,
            raw_csrf_token=new_raw_csrf,
        )

    async def logout(self, raw_refresh_token: str | None, raw_csrf_header: str | None) -> None:
        """Revoke current refresh session and return cleanly."""
        if not raw_refresh_token:
            return

        token_digest = hash_token(raw_refresh_token)
        now = datetime.now(UTC)

        session_obj = await self._refresh_repo.get_by_token_hash_for_update(token_digest)
        if session_obj is None:
            return

        # If rotated token reuse attempted during logout, compromise the family
        if session_obj.status == RefreshSessionStatus.ROTATED.value:
            await self._refresh_repo.revoke_family(session_obj.family_id, revoked_at=now)
            await self._session.commit()
            return

        if session_obj.status == RefreshSessionStatus.ACTIVE.value:
            # Validate CSRF header if present
            if raw_csrf_header and verify_csrf_token(raw_csrf_header, session_obj.csrf_token_hash):
                await self._refresh_repo.revoke_session(session_obj.id, revoked_at=now)
                await self._session.commit()
            else:
                # Even if CSRF fails, safely revoke session on explicit logout request
                await self._refresh_repo.revoke_session(session_obj.id, revoked_at=now)
                await self._session.commit()

    async def logout_all(self, user_id: UUID) -> None:
        """Revoke all refresh sessions for a user and increment auth_version."""
        now = datetime.now(UTC)
        await self._user_repo.increment_auth_version(user_id)
        await self._refresh_repo.revoke_all_user_sessions(user_id, revoked_at=now)
        await self._session.commit()

    async def get_me_profile(self, user_id: UUID, organization_id: UUID) -> UserProfileResponse:
        """Fetch safe user and organization profile details."""
        user = await self._user_repo.get_by_id(user_id)
        if user is None or user.status != UserStatus.ACTIVE.value:
            raise AuthServiceError(ServiceErrorCode.INVALID_TOKEN, "User account is not active.")

        mem_org = await self._user_repo.get_active_membership(user_id, organization_id)
        if mem_org is None:
            raise AuthServiceError(
                ServiceErrorCode.INVALID_TOKEN, "Organization membership is not active."
            )

        membership, org = mem_org
        return UserProfileResponse(
            id=user.id,
            email=user.email,
            display_name=user.display_name,
            status=user.status,
            organization_id=org.id,
            organization_name=org.name,
            organization_slug=org.slug,
            role=membership.role,
        )
