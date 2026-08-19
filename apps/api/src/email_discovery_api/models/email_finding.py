"""EmailFinding domain model representing a canonical email address discovered within a scan job."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    TIMESTAMP,
    Boolean,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from email_discovery_api.models.base import Base
from email_discovery_api.models.mixins import UUIDPrimaryKeyMixin, utc_now

if TYPE_CHECKING:
    from email_discovery_api.models.email_evidence import EmailEvidence
    from email_discovery_api.models.scan_job import ScanJob
    from email_discovery_api.models.scan_url import ScanURL


class EmailFinding(Base, UUIDPrimaryKeyMixin):
    """EmailFinding entity for storing unique canonical primary email findings.

    Scoped per ScanURL / ScanJob.
    """

    __tablename__ = "email_findings"
    __table_args__ = (
        ForeignKeyConstraint(
            ["scan_url_id", "scan_job_id"],
            ["scan_urls.id", "scan_urls.scan_job_id"],
            ondelete="CASCADE",
            name="fk_email_findings_scan_url_job",
        ),
        CheckConstraint(
            "canonical_email = LOWER(canonical_email)",
            name="ck_email_findings_canonical_email_lower",
        ),
        CheckConstraint(
            "email_domain = LOWER(email_domain)", name="ck_email_findings_email_domain_lower"
        ),
        CheckConstraint("evidence_count >= 0", name="ck_email_findings_evidence_count"),
        CheckConstraint("first_found_at <= last_found_at", name="ck_email_findings_timestamps"),
        Index("ix_email_findings_job_canonical", "scan_job_id", "canonical_email"),
        Index("ix_email_findings_job_classification", "scan_job_id", "classification"),
        Index("ix_email_findings_scan_url_id", "scan_url_id"),
        Index(
            "uq_email_findings_scan_url_not_null",
            "scan_url_id",
            unique=True,
            postgresql_where=text("scan_url_id IS NOT NULL"),
        ),
        Index(
            "uq_email_findings_historical_job_canonical",
            "scan_job_id",
            "canonical_email",
            unique=True,
            postgresql_where=text("scan_url_id IS NULL"),
        ),
    )

    scan_job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("scan_jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    scan_url_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    canonical_email: Mapped[str] = mapped_column(String(255), nullable=False)
    email_domain: Mapped[str] = mapped_column(String(255), nullable=False)
    classification: Mapped[str] = mapped_column(String(50), nullable=False)
    is_role_based: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    validation_status: Mapped[str] = mapped_column(String(50), nullable=False, default="UNVERIFIED")
    first_found_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=utc_now,
    )
    last_found_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=utc_now,
    )
    evidence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=utc_now,
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=utc_now,
    )

    # Relationships
    scan_job: Mapped[ScanJob] = relationship()
    scan_url: Mapped[ScanURL | None] = relationship(overlaps="scan_job")
    evidence_items: Mapped[list[EmailEvidence]] = relationship(
        back_populates="email_finding",
        cascade="all, delete-orphan",
    )
