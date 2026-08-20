"""Typed output models for operational load runs."""

from pydantic import BaseModel, Field


class LoadRunReport(BaseModel):
    size: int
    workers: int
    worker_concurrency: int
    elapsed_seconds: float = Field(ge=0)
    urls_per_second: float = Field(ge=0)
    pages_per_second: float = Field(ge=0)
    p50_latency_seconds: float = Field(ge=0)
    p95_latency_seconds: float = Field(ge=0)
    p99_latency_seconds: float = Field(ge=0)
    peak_active_tasks: int = Field(ge=0)
    peak_active_claims: int = Field(ge=0)
    peak_database_connections: int = Field(ge=0)
    redis_operations: int = Field(ge=0)
    redis_fallbacks: int = Field(ge=0)
    retry_total: int = Field(ge=0)
    failure_total: int = Field(ge=0)
    success_total: int = Field(ge=0)
    partial_total: int = Field(ge=0)
    peak_python_memory_bytes: int = Field(ge=0)
    result_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    csv_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    attempt_rows: int = Field(ge=0)
    finding_rows: int = Field(ge=0)
    duplicate_attempt_groups: int = Field(ge=0)
    duplicate_finding_groups: int = Field(ge=0)
    sequential_attempts: bool
    stale_fence_zero_writes: bool
    expired_fence_zero_writes: bool
    nonterminal_rows: int = Field(ge=0)
    uncleared_claims: int = Field(ge=0)
    job_counters_match: bool
    shutdown_clean: bool
