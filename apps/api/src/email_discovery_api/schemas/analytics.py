"""Pydantic schemas for tenant-scoped analytics overview."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AnalyticsPeriodEnum(StrEnum):
    """Supported time period filters for analytics aggregates."""

    SEVEN_DAYS = "7d"
    THIRTY_DAYS = "30d"
    NINETY_DAYS = "90d"
    ALL_TIME = "all"


class AnalyticsTimelinePoint(BaseModel):
    """Single date point in daily activity timeline."""

    model_config = ConfigDict(frozen=True)

    date: str = Field(description="UTC calendar date formatted as YYYY-MM-DD")
    scans_created: int = Field(default=0, ge=0, description="Scan jobs created on this date")
    emails_found: int = Field(default=0, ge=0, description="Emails first discovered on this date")


class RecentScanJobSummary(BaseModel):
    """Summary of a recently completed scan job."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    name: str | None
    status: str
    completed_at: datetime
    valid_input_count: int = Field(ge=0)
    completed_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)
    email_finding_count: int = Field(ge=0)


class AnalyticsOverviewResponse(BaseModel):
    """Tenant-scoped analytics overview response."""

    model_config = ConfigDict(frozen=True)

    period: AnalyticsPeriodEnum
    start_at: datetime | None = Field(
        default=None, description="UTC start boundary of first calendar day (null for all)"
    )
    end_at: datetime = Field(description="Current UTC execution timestamp boundary")

    # Metrics
    total_scans: int = Field(ge=0, description="Scan jobs created inside the selected UTC period")
    active_scans: int = Field(
        ge=0, description="Scan jobs currently in QUEUED, RUNNING, or CANCELLING"
    )
    websites_submitted: int = Field(
        ge=0,
        description="Sum of valid_input_count representing accepted deduplicated websites",
    )
    websites_processed: int = Field(ge=0, description="Sum of completed_count + failed_count")
    websites_completed: int = Field(
        ge=0,
        description="Sum of completed_count, including successful NO_EMAIL website outcomes",
    )
    websites_failed: int = Field(ge=0, description="Sum of failed_count")
    emails_discovered: int = Field(ge=0, description="Sum of persisted job email_finding_count")
    successful_processing_rate: float = Field(
        ge=0.0,
        le=100.0,
        description=(
            "completed_count / (completed_count + failed_count) * 100, or 0.0 if denominator is 0"
        ),
    )

    # Distributions
    status_distribution: dict[str, int] = Field(
        description="Complete map of scan job status enum keys to counts with zero defaults"
    )
    findings_by_classification: dict[str, int] = Field(
        description="Complete map of email classification enum keys to counts with zero defaults"
    )
    findings_by_validation_status: dict[str, int] = Field(
        description="Complete map of email validation status enum keys to counts with zero defaults"
    )

    # Activity & Summaries
    scan_activity_timeline: list[AnalyticsTimelinePoint] = Field(
        description="Zero-filled daily timeline points across period date boundaries"
    )
    recent_completed_scans: list[RecentScanJobSummary] = Field(
        description="Max 5 recent completed or completed-with-errors scan jobs"
    )
