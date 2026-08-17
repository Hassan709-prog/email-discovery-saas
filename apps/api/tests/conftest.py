"""Pytest fixtures shared across apps/api unit and integration tests."""

import pytest
from pydantic import SecretStr

from email_discovery_api.config import Settings


@pytest.fixture
def test_settings() -> Settings:
    """Fixture providing isolated Settings for test execution."""
    return Settings(
        app_name="test-api",
        environment="development",
        database_url=SecretStr("postgresql+asyncpg://mockuser:mockpass@localhost:5432/mock_db"),
        jwt_secret_key=SecretStr("test-secret-key-min-32-chars-long-for-testing-purposes"),
        db_health_timeout_seconds=1.0,
    )
