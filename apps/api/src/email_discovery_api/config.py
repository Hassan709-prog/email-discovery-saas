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

    # Authentication Settings
    jwt_secret_key: SecretStr = Field(
        default=SecretStr(
            "insecure-dev-jwt-secret-key-change-in-production-min-32-chars-for-safety"
        ),
        description="Secret key for signing JWT tokens",
    )
    jwt_algorithm: str = Field(default="HS256", description="JWT signing algorithm (fixed HS256)")
    jwt_issuer: str = Field(default="email-discovery-api", description="JWT issuer claim")
    jwt_audience: str = Field(default="email-discovery-app", description="JWT audience claim")
    access_token_ttl_minutes: int = Field(default=15, description="Access token TTL in minutes")
    refresh_token_ttl_days: int = Field(default=14, description="Refresh token TTL in days")
    auth_password_min_length: int = Field(default=12, description="Min password length")
    auth_password_max_length: int = Field(default=128, description="Max password length")
    auth_hash_concurrency_limit: int = Field(
        default=10, description="Max simultaneous password hash/verify tasks"
    )
    refresh_cookie_name: str = Field(
        default="refresh_token", description="HttpOnly refresh cookie name"
    )
    csrf_cookie_name: str = Field(default="csrf_token", description="CSRF cookie name")
    cookie_secure: bool = Field(
        default=False, description="Set Secure flag on auth cookies (must be true in prod)"
    )
    cookie_samesite: str = Field(
        default="lax", description="SameSite flag for auth cookies (lax/strict/none)"
    )
    cookie_domain: str | None = Field(default=None, description="Optional domain for auth cookies")
    clock_skew_seconds: float = Field(
        default=10.0, description="Allowed clock skew in seconds for JWT verification"
    )

    model_config = SettingsConfigDict(
        env_file=None,
        env_prefix="",
        extra="ignore",
        case_sensitive=False,
    )

    @field_validator("jwt_algorithm", mode="after")
    @classmethod
    def validate_jwt_algorithm(cls, v: str) -> str:
        if v.upper() != "HS256":
            raise ValueError("JWT_ALGORITHM must be fixed to 'HS256'")
        return v.upper()

    @field_validator("jwt_secret_key", mode="after")
    @classmethod
    def validate_jwt_secret_key(cls, v: SecretStr) -> SecretStr:
        secret = v.get_secret_value()
        if len(secret) < 32:
            raise ValueError("JWT_SECRET_KEY must be at least 32 characters long")
        return v

    @field_validator("cookie_samesite", mode="after")
    @classmethod
    def validate_cookie_samesite(cls, v: str) -> str:
        s = v.lower()
        if s not in ("lax", "strict", "none"):
            raise ValueError("COOKIE_SAMESITE must be 'lax', 'strict', or 'none'")
        return s

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

    def model_post_init(self, __context: object) -> None:
        """Validate production security requirements after initialization."""
        is_dev = self.environment.lower() == "development"
        if not is_dev:
            secret = self.jwt_secret_key.get_secret_value().lower()
            if any(
                term in secret for term in ("insecure", "change-in-production", "default", "secret")
            ):
                raise ValueError("Production JWT_SECRET_KEY cannot use default/insecure values")
            if not self.cookie_secure:
                raise ValueError("COOKIE_SECURE must be True outside development environment")

    def get_database_url_str(self) -> str:
        """Return raw database URL string containing unmasked credentials."""
        return self.database_url.get_secret_value()

    def __repr__(self) -> str:
        """Sanitizing string representation to prevent password and key leaks in logs."""
        masked = "*****"
        return (
            f"Settings(app_name={self.app_name!r}, environment={self.environment!r}, "
            f"debug={self.debug!r}, log_level={self.log_level!r}, "
            f"database_url=SecretStr('{masked}'), jwt_secret_key=SecretStr('{masked}'), "
            f"db_pool_size={self.db_pool_size}, db_max_overflow={self.db_max_overflow}, "
            f"db_health_timeout_seconds={self.db_health_timeout_seconds})"
        )


@lru_cache
def get_settings() -> Settings:
    """Return cached application Settings instance."""
    return Settings()
