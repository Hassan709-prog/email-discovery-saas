"""Crawl Worker Configuration settings using pydantic-settings."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class WorkerSettings(BaseSettings):
    """Worker settings read strictly from environment variables."""

    environment: str = Field(default="development", description="Execution environment")
    worker_id: str | None = Field(default=None, description="Worker identifier")
    concurrency: int = Field(default=2, description="Max concurrent scan tasks")
    poll_interval: float = Field(default=2.0, description="Default idle poll interval")
    healthy_poll_interval: float = Field(
        default=10.0, description="Healthy background poll interval"
    )
    degraded_poll_interval: float = Field(default=2.0, description="Degraded DB poll interval")
    lease_duration: float = Field(default=120.0, description="Lease duration in seconds")
    heartbeat_interval: float = Field(default=30.0, description="Heartbeat interval in seconds")

    # Redis Settings
    redis_url: SecretStr = Field(
        default=SecretStr("redis://localhost:6379/0"),
        description="Redis connection URL",
    )
    redis_required: bool = Field(default=False, description="Whether Redis is strictly required")
    redis_max_connections: int = Field(default=20, description="Max Redis pool connections")
    redis_connect_timeout: float = Field(default=2.0, description="Connect timeout in seconds")
    redis_socket_timeout: float = Field(default=2.0, description="Socket timeout in seconds")
    redis_operation_timeout: float = Field(default=0.5, description="Command timeout in seconds")
    redis_health_timeout: float = Field(default=1.0, description="Health probe timeout in seconds")
    redis_pubsub_reconnect_max_backoff: float = Field(
        default=30.0, description="Max Pub/Sub backoff in seconds"
    )
    redis_key_prefix: str = Field(
        default="email_discovery:v1:dev", description="Namespaced Redis key prefix"
    )
    redis_rate_limit_fallback_mode: str = Field(
        default="strict_pause",
        description="Fallback mode ('single_worker_local' or 'strict_pause')",
    )

    # Clamped Rate Limiting Parameters (ms)
    min_domain_interval_ms: int = Field(default=1000, description="Min domain interval ms")
    max_domain_interval_ms: int = Field(default=60000, description="Max domain interval ms")
    max_reservation_horizon_ms: int = Field(default=60000, description="Max reservation horizon ms")
    min_ttl_ms: int = Field(default=60000, description="Min TTL ms")
    max_ttl_ms: int = Field(default=86400000, description="Max TTL ms")
    safety_margin_ms: int = Field(default=5000, description="Safety margin ms")

    model_config = SettingsConfigDict(
        env_file=None,
        env_prefix="",
        extra="ignore",
        case_sensitive=False,
    )

    @field_validator("redis_rate_limit_fallback_mode", mode="after")
    @classmethod
    def validate_fallback_mode(cls, v: str) -> str:
        mode = v.lower()
        if mode not in ("single_worker_local", "strict_pause"):
            raise ValueError(
                "REDIS_RATE_LIMIT_FALLBACK_MODE must be 'single_worker_local' or 'strict_pause'"
            )
        return mode

    def model_post_init(self, __context: object) -> None:
        """Validate cross-field configuration invariant using max_domain_interval_ms."""
        required_ttl = (
            self.max_reservation_horizon_ms + self.max_domain_interval_ms + self.safety_margin_ms
        )
        if self.max_ttl_ms < required_ttl:
            raise ValueError(
                f"Invalid Redis rate limit configuration: max_ttl_ms ({self.max_ttl_ms}) "
                f"must be >= max_reservation_horizon_ms ({self.max_reservation_horizon_ms}) + "
                f"max_domain_interval_ms ({self.max_domain_interval_ms}) + "
                f"safety_margin_ms ({self.safety_margin_ms}) = {required_ttl}"
            )


@lru_cache
def get_worker_settings() -> WorkerSettings:
    """Return cached WorkerSettings instance."""
    return WorkerSettings()
