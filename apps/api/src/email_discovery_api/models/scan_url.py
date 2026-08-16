"""ScanURL domain model representing an individual URL target in a scan job."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    TIMESTAMP,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from email_discovery_api.models.base import Base
from email_discovery_api.models.enums import ScanURLStatus
from email_discovery_api.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from email_discovery_api.models.scan_job import ScanJob


class ScanURL(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """ScanURL entity representing an individual target URL in a ScanJob.

    Service Invariant:
        - duplicate_of_scan_url_id MUST reference a row within the same ScanJob.
        - normalized_url and normalized_domain are NOT globally unique across jobs.
        - Every raw original_input row is preserved regardless of validity/duplicate.
    """

    __tablename__ = "scan_urls"
    __table_args__ = (
        UniqueConstraint("scan_job_id", "original_index", name="uq_scan_urls_job_index"),
        Index("ix_scan_urls_job_status", "scan_job_id", "status"),
        Index("ix_scan_urls_status_next_retry", "status", "next_retry_at"),
        Index("ix_scan_urls_status_lease_expires", "status", "lease_expires_at"),
        CheckConstraint(
            "status IN ('INVALID', 'PENDING', 'QUEUED', 'LEASED', 'SCANNING', "
            "'RETRY_WAIT', 'COMPLETED', 'NO_EMAIL', 'FAILED', 'CANCELLED', 'DUPLICATE')",
            name="ck_scan_urls_status",
        ),
        CheckConstraint("original_index >= 0", name="ck_scan_urls_index_nonnegative"),
        CheckConstraint("attempt_count >= 0", name="ck_scan_urls_attempts_nonnegative"),
        CheckConstraint("max_attempts >= 0", name="ck_scan_urls_max_attempts_nonnegative"),
        CheckConstraint("attempt_count <= max_attempts", name="ck_scan_urls_attempts_le_max"),
        CheckConstraint(
            "duplicate_of_scan_url_id IS NULL OR duplicate_of_scan_url_id != id",
            name="ck_scan_urls_self_duplicate_prevented",
        ),
    )

    scan_job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("scan_jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    original_index: Mapped[int] = mapped_column(Integer, nullable=False)
    original_input: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    normalized_domain: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default=ScanURLStatus.PENDING.value,
    )

    duplicate_of_scan_url_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("scan_urls.id", ondelete="SET NULL"),
        nullable=True,
    )
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    last_error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_retry_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(String(255), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    started_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)

    # Relationships
    scan_job: Mapped[ScanJob] = relationship(back_populates="scan_urls")
    duplicate_of: Mapped[ScanURL | None] = relationship(remote_side="ScanURL.id")
