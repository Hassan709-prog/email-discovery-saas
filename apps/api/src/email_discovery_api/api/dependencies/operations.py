"""Explicit system-operator authorization dependency."""

from fastapi import Depends, HTTPException, status

from email_discovery_api.api.dependencies.identity import RequestPrincipal, get_current_principal
from email_discovery_api.config import Settings, get_settings


async def require_operations_enabled(
    settings: Settings = Depends(get_settings),
) -> None:
    """Hide every operations route unless explicitly enabled."""
    if not settings.operations_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found.")


async def require_operations_admin(
    principal: RequestPrincipal = Depends(get_current_principal),
    settings: Settings = Depends(get_settings),
) -> RequestPrincipal:
    """Require both enabled operations API and explicit user-ID allowlisting."""
    try:
        allowed = settings.get_operations_admin_user_ids()
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Operations authorization is unavailable.",
        ) from None
    if principal.user_id not in allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="System operator authorization is required.",
        )
    return principal
