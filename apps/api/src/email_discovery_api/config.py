"""Application configuration settings using pydantic-settings."""

import math
from functools import lru_cache

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings read strictly from environment variables."""

    environment: str = Field(default="development", description="Execution environment")
    app_name: str = Field(default="email-discovery-api", description="Application service name")
    debug: bool = Field(default=False, description="Enable debug mode")
    log_level: str = Field(default="INFO", description="Logging output level")
    allow_dev_identity_headers: bool = Field(
        default=False,
        validation_alias="ALLOW_DEV_IDENTITY_HEADERS",
        description="Allow development X-Dev-User-ID and X-Dev-Organization-ID identity headers",
    )

    # Database Settings
    database_url: SecretStr = Field(
        default=SecretStr("postgresql+asyncpg://postgres:postgres@localhost:5432/email_discovery"),
        description="Async PostgreSQL connection URL",
    )
    db_pool_size: int = Field(default=10, description="SQLAlchemy connection pool size")
    db_max_overflow: int = Field(default=20, description="SQLAlchemy connection pool max overflow")
    db_pool_timeout: float = Field(default=30.0, description="Connection pool timeout in seconds")
    db_pool_recycle: int = Field(
        default=1800, description="Connection pool recycle time in seconds"
    )
    db_health_timeout_seconds: float = Field(
        default=2.0, description="Readiness health check timeout in seconds"
    )

    model_config = SettingsConfigDict(
        env_file=None,
        env_prefix="",
        extra="ignore",
        case_sensitive=False,
    )

    @field_validator("database_url", mode="after")
    @classmethod
    def validate_database_url_scheme(cls, v: SecretStr) -> SecretStr:
        raw_url = v.get_secret_value()
        if not raw_url.startswith("postgresql+asyncpg://") and not raw_url.startswith(
            "postgresql+asyncpg:"
        ):
            raise ValueError("DATABASE_URL must use the 'postgresql+asyncpg' scheme")
        return v

    @field_validator("db_health_timeout_seconds", mode="after")
    @classmethod
    def validate_health_timeout(cls, v: float) -> float:
        if not math.isfinite(v) or v <= 0.0:
            raise ValueError("db_health_timeout_seconds must be a finite positive number")
        return v

    def get_database_url_str(self) -> str:
        """Return raw database URL string containing unmasked credentials."""
        return self.database_url.get_secret_value()

    def __repr__(self) -> str:
        """Sanitizing string representation to prevent password leaks in logs."""
        masked_url = "*****"
        return (
            f"Settings(app_name={self.app_name!r}, environment={self.environment!r}, "
            f"debug={self.debug!r}, log_level={self.log_level!r}, "
            f"database_url=SecretStr('{masked_url}'), db_pool_size={self.db_pool_size}, "
            f"db_max_overflow={self.db_max_overflow}, "
            f"db_health_timeout_seconds={self.db_health_timeout_seconds})"
        )


@lru_cache
def get_settings() -> Settings:
    """Return cached application Settings instance."""
    return Settings()
