"""Unit tests for email_discovery_api configuration and settings."""

import pytest
from pydantic import SecretStr

from email_discovery_api.config import Settings, get_settings


def test_default_settings() -> None:
    """Verify default application configuration parameters."""
    settings = Settings()
    assert settings.environment == "development"
    assert settings.app_name == "email-discovery-api"
    assert settings.debug is False
    assert settings.log_level == "INFO"
    assert settings.db_pool_size == 10
    assert settings.db_max_overflow == 20
    assert settings.db_health_timeout_seconds == 2.0


def test_database_url_secret_masking() -> None:
    """Verify raw password is hidden from string representations and logs."""
    raw_pass = "SuperSecretPassword123"
    url = f"postgresql+asyncpg://user:{raw_pass}@localhost:5432/email_discovery"
    settings = Settings(database_url=SecretStr(url))

    # SecretStr repr hides password
    repr_str = repr(settings)
    assert raw_pass not in repr_str
    assert "SuperSecretPassword123" not in str(settings.database_url)
    assert "*****" in repr_str

    # Unmasked URL string is accessible strictly via helper
    assert settings.get_database_url_str() == url


def test_database_url_scheme_validation() -> None:
    """Verify Settings rejects invalid database driver schemes."""
    with pytest.raises(ValueError, match=r"postgresql\+asyncpg"):
        Settings(
            database_url=SecretStr(
                "postgresql+psycopg2://postgres:postgres@localhost:5432/email_discovery"
            )
        )


def test_health_timeout_validation() -> None:
    """Verify Settings enforces positive finite readiness health check timeouts."""
    with pytest.raises(ValueError, match="finite positive number"):
        Settings(db_health_timeout_seconds=0.0)

    with pytest.raises(ValueError, match="finite positive number"):
        Settings(db_health_timeout_seconds=-1.5)

    with pytest.raises(ValueError, match="finite positive number"):
        Settings(db_health_timeout_seconds=float("nan"))


def test_environment_variable_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify environment variables override default settings values."""
    monkeypatch.setenv("APP_NAME", "custom-email-api")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("DB_POOL_SIZE", "25")

    settings = Settings()
    assert settings.app_name == "custom-email-api"
    assert settings.log_level == "DEBUG"
    assert settings.db_pool_size == 25


def test_get_settings_caching() -> None:
    """Verify get_settings returns a cached Settings instance."""
    s1 = get_settings()
    s2 = get_settings()
    assert s1 is s2
