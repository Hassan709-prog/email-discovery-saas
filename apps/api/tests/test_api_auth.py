"""HTTP API unit tests for authentication endpoints, cookies, CSRF, and identity resolution."""

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from email_discovery_api.api.dependencies.identity import RequestPrincipal, get_current_principal
from email_discovery_api.api.dependencies.services import get_auth_service
from email_discovery_api.main import create_app
from email_discovery_api.schemas.auth import (
    AuthSuccessResponse,
    OrganizationChoiceSchema,
    OrganizationSelectionRequiredResponse,
    UserProfileResponse,
)
from email_discovery_api.services.auth import (
    AuthService,
    AuthSuccessResult,
    OrganizationSelectionRequiredResult,
)


@pytest.fixture
def test_app() -> FastAPI:
    app = create_app()
    app.state.db_manager = MagicMock()
    return app


def test_register_route_sets_cookies_and_returns_201(test_app: FastAPI) -> None:
    """Verify POST /register returns 201 Created and attaches refresh & CSRF cookies."""
    client: Any = TestClient(test_app)
    mock_service = MagicMock(spec=AuthService)

    access_token = "mock-jwt-access-token-123"
    auth_result = AuthSuccessResult(
        response=AuthSuccessResponse(access_token=access_token, expires_in_seconds=900),
        raw_refresh_token="mock-refresh-token-xyz",
        raw_csrf_token="mock-csrf-token-abc",
    )
    mock_service.register = AsyncMock(return_value=auth_result)

    test_app.dependency_overrides[get_auth_service] = lambda: mock_service

    payload = {
        "email": "newuser@example.com",
        "password": "SecurePassword123!",
        "organization_name": "New Corp",
    }
    res = client.post("/api/v1/auth/register", json=payload)

    assert res.status_code == 201
    data = res.json()
    assert data["access_token"] == access_token
    assert data["token_type"] == "Bearer"

    # Verify cookies attached
    assert "refresh_token" in res.cookies
    assert "csrf_token" in res.cookies
    assert res.cookies["refresh_token"] == "mock-refresh-token-xyz"
    assert res.cookies["csrf_token"] == "mock-csrf-token-abc"


def test_login_route_success(test_app: FastAPI) -> None:
    """Verify POST /api/v1/auth/login returns 200 OK and sets cookies."""
    client: Any = TestClient(test_app)
    mock_service = MagicMock(spec=AuthService)

    access_token = "mock-jwt-access-token-456"
    auth_result = AuthSuccessResult(
        response=AuthSuccessResponse(access_token=access_token, expires_in_seconds=900),
        raw_refresh_token="mock-refresh-456",
        raw_csrf_token="mock-csrf-456",
    )
    mock_service.login = AsyncMock(return_value=auth_result)

    test_app.dependency_overrides[get_auth_service] = lambda: mock_service

    payload = {"email": "user@example.com", "password": "ValidPassword123!"}
    res = client.post("/api/v1/auth/login", json=payload)

    assert res.status_code == 200
    assert res.json()["access_token"] == access_token
    assert "refresh_token" in res.cookies


def test_login_multi_tenant_selection_required(test_app: FastAPI) -> None:
    """Verify POST /api/v1/auth/login returns 400 selection required without issuing cookies."""
    client: Any = TestClient(test_app)
    mock_service = MagicMock(spec=AuthService)

    org1_id = uuid.uuid4()
    org2_id = uuid.uuid4()

    choices = [
        OrganizationChoiceSchema(id=org1_id, name="Org 1", slug="org-1", role="OWNER"),
        OrganizationChoiceSchema(id=org2_id, name="Org 2", slug="org-2", role="MEMBER"),
    ]
    result = OrganizationSelectionRequiredResult(
        response=OrganizationSelectionRequiredResponse(
            organization_selection_required=True,
            organizations=choices,
        )
    )
    mock_service.login = AsyncMock(return_value=result)

    test_app.dependency_overrides[get_auth_service] = lambda: mock_service

    res = client.post(
        "/api/v1/auth/login", json={"email": "multi@example.com", "password": "ValidPassword123!"}
    )

    assert res.status_code == 400
    data = res.json()
    assert data["organization_selection_required"] is True
    assert len(data["organizations"]) == 2
    assert "refresh_token" not in res.cookies


def test_logout_returns_empty_204_and_clears_cookies(test_app: FastAPI) -> None:
    """Verify POST /api/v1/auth/logout returns empty 204 No Content response and clears cookies."""
    client: Any = TestClient(test_app)
    mock_service = MagicMock(spec=AuthService)
    mock_service.logout = AsyncMock()

    test_app.dependency_overrides[get_auth_service] = lambda: mock_service

    # Pass refresh cookie and CSRF header
    client.cookies.set("refresh_token", "old-refresh-token", path="/api/v1/auth")
    client.cookies.set("csrf_token", "old-csrf-token", path="/api/v1/auth")

    res = client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": "old-csrf-token"})

    assert res.status_code == 204
    assert res.content == b""  # Empty 204 response body


def test_logout_all_returns_empty_204(test_app: FastAPI) -> None:
    """Verify POST /api/v1/auth/logout-all requires Bearer token and returns empty 204."""
    client: Any = TestClient(test_app)
    mock_service = MagicMock(spec=AuthService)
    mock_service.logout_all = AsyncMock()

    user_id = uuid.uuid4()
    org_id = uuid.uuid4()
    test_principal = RequestPrincipal(user_id=user_id, organization_id=org_id)

    test_app.dependency_overrides[get_current_principal] = lambda: test_principal
    test_app.dependency_overrides[get_auth_service] = lambda: mock_service

    res = client.post("/api/v1/auth/logout-all")

    assert res.status_code == 204
    assert res.content == b""
    mock_service.logout_all.assert_awaited_once_with(user_id)


def test_get_me_profile_success(test_app: FastAPI) -> None:
    """Verify GET /api/v1/auth/me returns safe profile info without secrets."""
    client: Any = TestClient(test_app)
    mock_service = MagicMock(spec=AuthService)

    user_id = uuid.uuid4()
    org_id = uuid.uuid4()
    profile = UserProfileResponse(
        id=user_id,
        email="me@example.com",
        display_name="My Name",
        status="ACTIVE",
        organization_id=org_id,
        organization_name="My Org",
        organization_slug="my-org",
        role="OWNER",
    )
    mock_service.get_me_profile = AsyncMock(return_value=profile)

    test_app.dependency_overrides[get_current_principal] = lambda: RequestPrincipal(
        user_id=user_id, organization_id=org_id
    )
    test_app.dependency_overrides[get_auth_service] = lambda: mock_service

    res = client.get("/api/v1/auth/me")

    assert res.status_code == 200
    data = res.json()
    assert data["id"] == str(user_id)
    assert data["email"] == "me@example.com"
    assert data["role"] == "OWNER"
    assert "password_hash" not in data
    assert "token_hash" not in data
    assert "auth_version" not in data
