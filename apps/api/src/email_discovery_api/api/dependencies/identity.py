"""Identity and RequestPrincipal dependency abstractions."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from email_discovery_api.config import Settings, get_settings
from email_discovery_api.database import get_db_session
from email_discovery_api.models import UserStatus
from email_discovery_api.repositories.users import UserRepository
from email_discovery_api.services.tokens import InvalidTokenError, TokenService

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)


@dataclass(frozen=True)
class RequestPrincipal:
    """Immutable principal containing authenticated tenant identity and request ID."""

    user_id: UUID
    organization_id: UUID
    request_id: str | None = None


async def get_current_principal(
    request: Request,
    token: str | None = Depends(oauth2_scheme),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> RequestPrincipal:
    """Dependency extracting OAuth2 Bearer token, confirming identity, or raising HTTP 401."""
    request_id = getattr(request.state, "request_id", None)

    # 1. OAuth2 Bearer token path
    if token:
        token_service = TokenService(settings)
        try:
            payload = token_service.decode_access_token(token)
        except InvalidTokenError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired access token.",
                headers={"WWW-Authenticate": "Bearer"},
            ) from None

        user_repo = UserRepository(session)
        user = await user_repo.get_by_id(payload.user_id)

        if user is None or user.status != UserStatus.ACTIVE.value:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User account is inactive or not found.",
                headers={"WWW-Authenticate": "Bearer"},
            )

        if user.auth_version != payload.auth_version:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Access token has been invalidated by a security event.",
                headers={"WWW-Authenticate": "Bearer"},
            )

        mem_org = await user_repo.get_active_membership(user.id, payload.organization_id)
        if mem_org is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Tenant organization membership is inactive.",
                headers={"WWW-Authenticate": "Bearer"},
            )

        _, org = mem_org
        return RequestPrincipal(
            user_id=user.id,
            organization_id=org.id,
            request_id=request_id,
        )

    # 2. Optional development identity mode path
    if settings.allow_dev_identity_headers:
        env = settings.environment.lower()
        if env in ("production", "staging"):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Development identity mode is forbidden outside development environment.",
                headers={"WWW-Authenticate": "Bearer"},
            )

        dev_user_header = request.headers.get("X-Dev-User-ID")
        dev_org_header = request.headers.get("X-Dev-Organization-ID")

        if dev_user_header and dev_org_header:
            try:
                user_id = UUID(dev_user_header.strip())
                org_id = UUID(dev_org_header.strip())
                return RequestPrincipal(
                    user_id=user_id,
                    organization_id=org_id,
                    request_id=request_id,
                )
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid development identity header format.",
                    headers={"WWW-Authenticate": "Bearer"},
                ) from None

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication credentials were not provided.",
        headers={"WWW-Authenticate": "Bearer"},
    )
