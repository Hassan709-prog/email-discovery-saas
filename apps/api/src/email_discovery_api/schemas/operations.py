"""Stable privacy-safe schemas for system operations."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class DependencyReadiness(BaseModel):
    postgresql: Literal["ok", "unavailable"]
    redis: Literal["ok", "degraded", "unavailable"]
    redis_required: bool


class WorkerStateCount(BaseModel):
    state: str
    count: int = Field(ge=0)


class AdvisoryWorkerPresence(BaseModel):
    instance_digest: str = Field(pattern=r"^[0-9a-f]{16}$")
    state: str
    configured_concurrency: int = Field(ge=0)
    active_claims: int = Field(ge=0)
    last_seen_at: datetime
    stale: bool


class WorkerOperationalMetrics(BaseModel):
    source: Literal["redis_advisory"] = "redis_advisory"
    present: int = Field(ge=0)
    stale: int = Field(ge=0)
    configured_concurrency: int = Field(ge=0)
    active_claims: int = Field(ge=0)
    states: list[WorkerStateCount]
    records: list[AdvisoryWorkerPresence]


class URLOperationalMetrics(BaseModel):
    queued: int = Field(ge=0)
    leased: int = Field(ge=0)
    scanning: int = Field(ge=0)
    retry_wait: int = Field(ge=0)
    completed: int = Field(ge=0)
    no_email: int = Field(ge=0)
    failed: int = Field(ge=0)
    expired_leases: int = Field(ge=0)
    oldest_queued_age_seconds: float | None = Field(default=None, ge=0)
    retry_total: int = Field(ge=0)
    recent_terminal_count: int = Field(ge=0)
    recent_throughput_per_second: float = Field(ge=0)
    failure_reasons: list[WorkerStateCount]


class JobOperationalMetrics(BaseModel):
    active: int = Field(ge=0)
    terminal: int = Field(ge=0)
    oldest_active_age_seconds: float | None = Field(default=None, ge=0)


class OperationalMetricsResponse(BaseModel):
    generated_at: datetime
    window_seconds: int
    readiness: DependencyReadiness
    workers: WorkerOperationalMetrics
    urls: URLOperationalMetrics
    jobs: JobOperationalMetrics


class DiagnosticItem(BaseModel):
    reference_digest: str = Field(pattern=r"^[0-9a-f]{16}$")
    reason: str


class DiagnosticCategory(BaseModel):
    total: int = Field(ge=0)
    truncated: bool
    items: list[DiagnosticItem]


class OperationalDiagnosticsResponse(BaseModel):
    generated_at: datetime
    read_only: Literal[True] = True
    jobs_eligible_for_finalization: DiagnosticCategory
    nonterminal_jobs_without_active_work: DiagnosticCategory
    expired_leases: DiagnosticCategory
    due_retries: DiagnosticCategory
    counter_mismatches: DiagnosticCategory
    expired_worker_presence: DiagnosticCategory


class RecoveryRequest(BaseModel):
    confirm: Literal[True]


class RecoveryResponse(BaseModel):
    reference_digest: str = Field(pattern=r"^[0-9a-f]{16}$")
    outcome: Literal["reconciled", "not_found"]
    audit_recorded: bool
