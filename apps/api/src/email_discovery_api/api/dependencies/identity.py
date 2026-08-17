"""Identity and RequestPrincipal dependency abstractions."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status

from email_discovery_api.config import Settings, get_settings


@dataclass(frozen=True)
class RequestPrincipal:
    """Immutable principal containing authenticated tenant identity and request ID."""

    user_id: UUID
    organization_id: UUID
    request_id: str | None = None


async def get_current_principal(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> RequestPrincipal:
    """Dependency returning authenticated principal or raising HTTP 401.

    By default, requires explicit authentication or test dependency override.
    Optional development identity headers (X-Dev-User-ID, X-Dev-Organization-ID) are permitted
    only when ALLOW_DEV_IDENTITY_HEADERS=true AND environment is development.
    """
    request_id = getattr(request.state, "request_id", None)

    if settings.allow_dev_identity_headers:
        env = settings.environment.lower()
        if env in ("production", "staging"):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Development identity mode is forbidden outside development environment.",
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
                ) from None

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication credentials were not provided.",
        headers={"WWW-Authenticate": "Bearer"},
    )
