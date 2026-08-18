"""Authentication and session management HTTP API routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, Request, Response, status
from fastapi.responses import JSONResponse

from email_discovery_api.api.dependencies.identity import RequestPrincipal, get_current_principal
from email_discovery_api.api.dependencies.services import get_auth_service
from email_discovery_api.config import Settings, get_settings
from email_discovery_api.schemas.auth import (
    AuthSuccessResponse,
    LoginRequest,
    RegisterRequest,
    UserProfileResponse,
)
from email_discovery_api.services.auth import (
    AuthService,
    AuthServiceError,
    AuthSuccessResult,
    ServiceErrorCode,
)

router = APIRouter(prefix="/api/v1/auth", tags=["Authentication"])


def set_auth_cookies(
    response: Response,
    raw_refresh_token: str,
    raw_csrf_token: str,
    settings: Settings,
) -> None:
    """Attach secure HttpOnly refresh token cookie and readable CSRF token cookie."""
    max_age = settings.refresh_token_ttl_days * 86400
    samesite_val: Any = settings.cookie_samesite
    response.set_cookie(
        key=settings.refresh_cookie_name,
        value=raw_refresh_token,
        max_age=max_age,
        path="/api/v1/auth",
        domain=settings.cookie_domain,
        secure=settings.cookie_secure,
        httponly=True,
        samesite=samesite_val,
    )
    response.set_cookie(
        key=settings.csrf_cookie_name,
        value=raw_csrf_token,
        max_age=max_age,
        path="/",
        domain=settings.cookie_domain,
        secure=settings.cookie_secure,
        httponly=False,
        samesite=samesite_val,
    )


def clear_auth_cookies(
    response: Response,
    settings: Settings,
) -> None:
    """Clear refresh and CSRF cookies using identical security attributes."""
    samesite_val: Any = settings.cookie_samesite
    response.delete_cookie(
        key=settings.refresh_cookie_name,
        path="/api/v1/auth",
        domain=settings.cookie_domain,
        secure=settings.cookie_secure,
        httponly=True,
        samesite=samesite_val,
    )
    response.delete_cookie(
        key=settings.csrf_cookie_name,
        path="/",
        domain=settings.cookie_domain,
        secure=settings.cookie_secure,
        httponly=False,
        samesite=samesite_val,
    )


@router.post(
    "/register",
    status_code=status.HTTP_201_CREATED,
    response_model=AuthSuccessResponse,
    summary="Self-register a new user and organization",
)
async def register(
    request: Request,
    payload: RegisterRequest,
    response: Response,
    auth_service: AuthService = Depends(get_auth_service),
    settings: Settings = Depends(get_settings),
) -> AuthSuccessResponse:
    """Create new user account, organization, owner membership, and return initial credentials."""
    request_id = getattr(request.state, "request_id", "system")
    result = await auth_service.register(payload, request_id=request_id)
    set_auth_cookies(response, result.raw_refresh_token, result.raw_csrf_token, settings)
    return result.response


@router.post(
    "/login",
    summary="Authenticate user with email and password",
)
async def login(
    request: Request,
    payload: LoginRequest,
    response: Response,
    auth_service: AuthService = Depends(get_auth_service),
    settings: Settings = Depends(get_settings),
) -> Any:
    """Authenticate user credentials and issue session tokens or request organization selection."""
    request_id = getattr(request.state, "request_id", "system")
    result = await auth_service.login(payload, request_id=request_id)

    if isinstance(result, AuthSuccessResult):
        set_auth_cookies(response, result.raw_refresh_token, result.raw_csrf_token, settings)
        return result.response

    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content=result.response.model_dump(mode="json"),
    )


@router.post(
    "/refresh",
    response_model=AuthSuccessResponse,
    summary="Rotate refresh session and issue new access token",
)
async def refresh(
    request: Request,
    response: Response,
    csrf_header: str | None = Header(None, alias="X-CSRF-Token"),
    auth_service: AuthService = Depends(get_auth_service),
    settings: Settings = Depends(get_settings),
) -> AuthSuccessResponse:
    """Rotate active refresh token, validate CSRF binding, and issue updated access token."""
    raw_refresh_token = request.cookies.get(settings.refresh_cookie_name)
    if not raw_refresh_token:
        clear_auth_cookies(response, settings)
        raise AuthServiceError(ServiceErrorCode.INVALID_TOKEN, "Refresh token cookie not provided.")

    try:
        result = await auth_service.refresh(raw_refresh_token, csrf_header)
    except AuthServiceError:
        clear_auth_cookies(response, settings)
        raise

    set_auth_cookies(response, result.raw_refresh_token, result.raw_csrf_token, settings)
    return result.response


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Logout user and revoke refresh session",
)
async def logout(
    request: Request,
    response: Response,
    csrf_header: str | None = Header(None, alias="X-CSRF-Token"),
    auth_service: AuthService = Depends(get_auth_service),
    settings: Settings = Depends(get_settings),
) -> Response:
    """Revoke current refresh session and clear authentication cookies."""
    raw_refresh_token = request.cookies.get(settings.refresh_cookie_name)
    await auth_service.logout(raw_refresh_token, csrf_header)
    clear_auth_cookies(response, settings)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.post(
    "/logout-all",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke all sessions and invalidate all issued access tokens",
)
async def logout_all(
    response: Response,
    principal: RequestPrincipal = Depends(get_current_principal),
    auth_service: AuthService = Depends(get_auth_service),
    settings: Settings = Depends(get_settings),
) -> Response:
    """Increment user auth_version and revoke all user refresh sessions across all devices."""
    await auth_service.logout_all(principal.user_id)
    clear_auth_cookies(response, settings)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get(
    "/me",
    response_model=UserProfileResponse,
    summary="Get current authenticated user profile",
)
async def get_me(
    principal: RequestPrincipal = Depends(get_current_principal),
    auth_service: AuthService = Depends(get_auth_service),
) -> UserProfileResponse:
    """Return identity and membership information for the authenticated principal."""
    return await auth_service.get_me_profile(
        user_id=principal.user_id,
        organization_id=principal.organization_id,
    )
