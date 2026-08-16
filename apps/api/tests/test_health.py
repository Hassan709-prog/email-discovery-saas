"""Unit and integration tests for application health endpoints and DB lifecycle."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from email_discovery_api.config import Settings
from email_discovery_api.database import DatabaseManager, get_db_session
from email_discovery_api.logging import is_valid_request_id
from email_discovery_api.main import create_app


@pytest.fixture
def test_settings() -> Settings:
    """Fixture providing isolated Settings without real database access."""
    return Settings(
        app_name="test-api",
        database_url=SecretStr("postgresql+asyncpg://mockuser:mockpass@localhost:5432/mock_db"),
        db_health_timeout_seconds=1.0,
    )


@pytest.fixture
def mock_db_manager(test_settings: Settings) -> DatabaseManager:
    """Fixture creating a DatabaseManager with mocked SQLAlchemy engine and session factory."""
    db_mgr = DatabaseManager.__new__(DatabaseManager)
    db_mgr.settings = test_settings
    db_mgr.engine = AsyncMock()
    db_mgr.session_factory = MagicMock()
    db_mgr.check_health = AsyncMock(return_value=True)  # pyright: ignore[reportAttributeAccessIssue]
    db_mgr.close = AsyncMock()  # pyright: ignore[reportAttributeAccessIssue]
    return db_mgr


@pytest.fixture
def test_app(test_settings: Settings, mock_db_manager: DatabaseManager) -> FastAPI:
    """Fixture creating a test FastAPI app with injected settings and mock DatabaseManager."""
    app = create_app(test_settings)
    app.state.settings = test_settings
    app.state.db_manager = mock_db_manager
    return app


@pytest.mark.anyio
async def test_liveness_probe(test_app: FastAPI) -> None:
    """Verify /health/live returns HTTP 200 without connecting to PostgreSQL."""
    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as client:
        resp = await client.get("/health/live")

    assert resp.status_code == 200
    data = resp.json()
    assert data == {"status": "ok", "service": "test-api"}
    assert "X-Request-ID" in resp.headers


@pytest.mark.anyio
async def test_readiness_probe_healthy(test_app: FastAPI, mock_db_manager: DatabaseManager) -> None:
    """Verify /health/ready returns HTTP 200 when database check succeeds."""
    mock_db_manager.check_health = AsyncMock(return_value=True)  # pyright: ignore[reportAttributeAccessIssue]

    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as client:
        resp = await client.get("/health/ready")

    assert resp.status_code == 200
    data = resp.json()
    assert data == {
        "status": "ok",
        "service": "test-api",
        "dependencies": {"database": "ok"},
    }


@pytest.mark.anyio
async def test_readiness_probe_unhealthy(
    test_app: FastAPI, mock_db_manager: DatabaseManager
) -> None:
    """Verify /health/ready returns HTTP 503 when database check fails or times out."""
    mock_db_manager.check_health = AsyncMock(return_value=False)  # pyright: ignore[reportAttributeAccessIssue]

    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as client:
        resp = await client.get("/health/ready")

    assert resp.status_code == 503
    data = resp.json()
    assert data == {
        "status": "unavailable",
        "service": "test-api",
        "dependencies": {"database": "unavailable"},
    }


@pytest.mark.anyio
async def test_readiness_probe_no_secret_leaks(
    test_app: FastAPI, mock_db_manager: DatabaseManager
) -> None:
    """Verify 503 error payload contains no passwords, database URLs, or tracebacks."""
    mock_db_manager.check_health = AsyncMock(return_value=False)  # pyright: ignore[reportAttributeAccessIssue]

    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as client:
        resp = await client.get("/health/ready")

    raw_text = resp.text
    assert "mockpass" not in raw_text
    assert "mockuser" not in raw_text
    assert "postgresql" not in raw_text
    assert "Traceback" not in raw_text


def test_request_id_validation() -> None:
    """Test request ID validation regex boundaries."""
    assert is_valid_request_id("req_12345_abc") is True
    assert is_valid_request_id("UUID-98765-XYZ") is True
    assert is_valid_request_id("") is False
    assert is_valid_request_id(None) is False
    assert is_valid_request_id("<script>alert(1)</script>") is False
    assert is_valid_request_id("invalid spaces in header") is False
    assert is_valid_request_id("a" * 129) is False  # > 128 chars limit


@pytest.mark.anyio
async def test_request_id_generation_and_preservation(test_app: FastAPI) -> None:
    """Verify request ID generation, preservation, and replacement."""
    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as client:
        # 1. Missing request ID
        r1 = await client.get("/health/live")
        req_id1 = r1.headers.get("X-Request-ID")
        assert req_id1 is not None
        assert len(req_id1) > 10

        # 2. Valid caller request ID
        r2 = await client.get("/health/live", headers={"X-Request-ID": "custom-caller-id-999"})
        assert r2.headers.get("X-Request-ID") == "custom-caller-id-999"

        # 3. Invalid / malicious request ID replaced
        r3 = await client.get("/health/live", headers={"X-Request-ID": "<bad_header_value>"})
        req_id3 = r3.headers.get("X-Request-ID")
        assert req_id3 is not None
        assert req_id3 != "<bad_header_value>"


@pytest.mark.anyio
async def test_get_db_session_lifecycle(test_app: FastAPI) -> None:
    """Verify get_db_session dependency yields session, rolls back on error, and closes cleanly."""
    mock_session = AsyncMock()
    mock_cm = AsyncMock()
    mock_cm.__aenter__.return_value = mock_session
    mock_factory = MagicMock(return_value=mock_cm)
    test_app.state.db_manager.session_factory = mock_factory

    mock_request = MagicMock()
    mock_request.app = test_app

    # Normal yield and cleanup
    gen = get_db_session(mock_request)
    session = await anext(gen)
    assert session is mock_session

    try:
        await anext(gen)
    except StopAsyncIteration:
        pass

    mock_session.close.assert_called_once()

    # Exception rollback and cleanup
    mock_session_fail = AsyncMock()
    mock_cm_fail = AsyncMock()
    mock_cm_fail.__aenter__.return_value = mock_session_fail
    mock_factory_fail = MagicMock(return_value=mock_cm_fail)
    test_app.state.db_manager.session_factory = mock_factory_fail

    gen_fail = get_db_session(mock_request)
    await anext(gen_fail)

    with pytest.raises(RuntimeError, match="Service Error"):
        try:
            raise RuntimeError("Service Error")
        except Exception as exc:
            await gen_fail.athrow(exc)

    mock_session_fail.rollback.assert_called_once()
    mock_session_fail.close.assert_called_once()


@pytest.mark.anyio
async def test_app_lifespan_disposes_engine() -> None:
    """Verify application lifespan creates and disposes DatabaseManager engine on shutdown."""
    settings = Settings(
        database_url=SecretStr(
            "postgresql+asyncpg://postgres:postgres@localhost:5432/email_discovery"
        )
    )
    app = create_app(settings)

    async with app.router.lifespan_context(app):
        assert app.state.db_manager is not None
        mock_close = AsyncMock()
        app.state.db_manager.close = mock_close

    mock_close.assert_called_once()
