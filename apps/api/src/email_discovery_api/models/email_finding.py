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
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from email_discovery_api.models.base import Base
from email_discovery_api.models.mixins import UUIDPrimaryKeyMixin, utc_now

if TYPE_CHECKING:
    from email_discovery_api.models.email_evidence import EmailEvidence
    from email_discovery_api.models.scan_job import ScanJob


class EmailFinding(Base, UUIDPrimaryKeyMixin):
    """EmailFinding entity for storing unique canonical email findings per ScanJob.

    Security & Privacy Note:
        Canonical email addresses are stored in bounded, lowercase format.
        first_found_at is set once on initial discovery and preserved across resubmissions.
    """

    __tablename__ = "email_findings"
    __table_args__ = (
        UniqueConstraint("scan_job_id", "canonical_email", name="uq_email_findings_job_canonical"),
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
    )

    scan_job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("scan_jobs.id", ondelete="CASCADE"),
        nullable=False,
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
    evidence_items: Mapped[list[EmailEvidence]] = relationship(
        back_populates="email_finding",
        cascade="all, delete-orphan",
    )
