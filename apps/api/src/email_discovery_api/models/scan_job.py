"""ScanJob domain model representing a batch execution request."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import TIMESTAMP, CheckConstraint, ForeignKey, Index, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from email_discovery_api.models.base import Base
from email_discovery_api.models.enums import ScanJobSourceType, ScanJobStatus
from email_discovery_api.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from email_discovery_api.models.job_event import JobEvent
    from email_discovery_api.models.organization import Organization
    from email_discovery_api.models.scan_url import ScanURL
    from email_discovery_api.models.user import User


class ScanJob(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Batch scan execution entity.

    Tenant Isolation Invariant:
        Every future repository/service query MUST require organization_id.
        No unscoped job or URL queries are permitted.
        The service layer must verify created_by_user_id belongs to organization_id.

    Input Counter Definition:
        - total_input_count: Total raw input rows submitted.
        - duplicate_input_count: Input rows determined to be duplicates within job.
        - valid_input_count: Valid unique input rows to be processed
          (valid_input_count + duplicate_input_count <= total_input_count).
        - queued_count, running_count, completed_count, failed_count: Processed state.
    """

    __tablename__ = "scan_jobs"
    __table_args__ = (
        Index("ix_scan_jobs_org_created", "organization_id", "created_at"),
        Index("ix_scan_jobs_org_status", "organization_id", "status"),
        Index(
            "uq_scan_jobs_org_idempotency",
            "organization_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
        CheckConstraint(
            "status IN ('DRAFT', 'QUEUED', 'RUNNING', 'CANCELLING', 'CANCELLED', "
            "'COMPLETED', 'COMPLETED_WITH_ERRORS', 'FAILED')",
            name="ck_scan_jobs_status",
        ),
        CheckConstraint(
            "source_type IN ('MANUAL', 'CSV', 'XLSX', 'API')",
            name="ck_scan_jobs_source_type",
        ),
        CheckConstraint("total_input_count >= 0", name="ck_scan_jobs_total_nonnegative"),
        CheckConstraint("valid_input_count >= 0", name="ck_scan_jobs_valid_nonnegative"),
        CheckConstraint("duplicate_input_count >= 0", name="ck_scan_jobs_duplicate_nonnegative"),
        CheckConstraint("queued_count >= 0", name="ck_scan_jobs_queued_nonnegative"),
        CheckConstraint("running_count >= 0", name="ck_scan_jobs_running_nonnegative"),
        CheckConstraint("completed_count >= 0", name="ck_scan_jobs_completed_nonnegative"),
        CheckConstraint("failed_count >= 0", name="ck_scan_jobs_failed_nonnegative"),
        CheckConstraint("email_finding_count >= 0", name="ck_scan_jobs_email_findings_nonnegative"),
        CheckConstraint(
            "valid_input_count <= total_input_count",
            name="ck_scan_jobs_valid_le_total",
        ),
        CheckConstraint(
            "duplicate_input_count <= total_input_count",
            name="ck_scan_jobs_duplicate_le_total",
        ),
        CheckConstraint(
            "valid_input_count + duplicate_input_count <= total_input_count",
            name="ck_scan_jobs_valid_dup_le_total",
        ),
        CheckConstraint(
            "queued_count + running_count + completed_count + failed_count <= valid_input_count",
            name="ck_scan_jobs_processed_le_valid",
        ),
        CheckConstraint(
            "next_event_sequence >= 1",
            name="ck_scan_jobs_next_event_seq_positive",
        ),
        CheckConstraint(
            "request_fingerprint IS NULL OR "
            "(length(request_fingerprint) = 64 AND request_fingerprint ~ '^[0-9a-f]{64}$')",
            name="ck_scan_jobs_fingerprint_hex",
        ),
        CheckConstraint(
            "idempotency_key IS NULL OR request_fingerprint IS NOT NULL",
            name="ck_scan_jobs_idempotency_fingerprint_pair",
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default=ScanJobStatus.DRAFT.value,
    )
    source_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default=ScanJobSourceType.MANUAL.value,
    )
    scanner_version: Mapped[str] = mapped_column(String(50), nullable=False, default="1.0.0")
    normalization_version: Mapped[str] = mapped_column(String(50), nullable=False, default="1.0.0")
    ranking_version: Mapped[str] = mapped_column(String(50), nullable=False, default="1.0.0")
    configuration_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )

    # Idempotency & Event Sequencing
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    request_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    next_event_sequence: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )

    # Counters
    total_input_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    valid_input_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duplicate_input_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    queued_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    running_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    email_finding_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Lifecycle Timestamps
    started_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    cancellation_requested_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    last_claimed_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )

    # Relationships
    organization: Mapped[Organization] = relationship(back_populates="scan_jobs")
    created_by_user: Mapped[User | None] = relationship()
    scan_urls: Mapped[list[ScanURL]] = relationship(
        back_populates="scan_job",
        cascade="all, delete-orphan",
    )
    events: Mapped[list[JobEvent]] = relationship(
        back_populates="scan_job",
        cascade="all, delete-orphan",
    )
