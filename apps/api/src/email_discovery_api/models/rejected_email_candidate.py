"""RejectedEmailCandidate domain model for audit tracking of rejected candidates."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    TIMESTAMP,
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from email_discovery_api.models.base import Base
from email_discovery_api.models.mixins import UUIDPrimaryKeyMixin, utc_now

if TYPE_CHECKING:
    from email_discovery_api.models.crawled_page import CrawledPage
    from email_discovery_api.models.scan_job import ScanJob
    from email_discovery_api.models.scan_url import ScanURL


class RejectedEmailCandidate(Base, UUIDPrimaryKeyMixin):
    """RejectedEmailCandidate entity for auditing extraction rejections.

    Privacy & Security Note:
        Raw rejected strings are NEVER stored unmasked. Candidate strings are masked
        (e.g., j***e@domain.com) prior to storage to protect potential user privacy.
    """

    __tablename__ = "rejected_email_candidates"
    __table_args__ = (
        UniqueConstraint(
            "scan_job_id",
            "candidate_hash",
            "rejection_code",
            name="uq_rejected_candidates_job_hash_code",
        ),
        CheckConstraint(
            "candidate_hash ~ '^[0-9a-f]{64}$'",
            name="ck_rejected_candidates_hash_hex",
        ),
        Index("ix_rejected_candidates_job_created", "scan_job_id", "created_at"),
    )

    scan_job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("scan_jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    scan_url_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("scan_urls.id", ondelete="CASCADE"),
        nullable=False,
    )
    crawled_page_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("crawled_pages.id", ondelete="SET NULL"),
        nullable=True,
    )
    candidate_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    masked_candidate: Mapped[str | None] = mapped_column(String(255), nullable=True)
    rejection_code: Mapped[str] = mapped_column(String(50), nullable=False)
    source_type: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=utc_now,
    )

    # Relationships
    scan_job: Mapped[ScanJob] = relationship()
    scan_url: Mapped[ScanURL] = relationship()
    crawled_page: Mapped[CrawledPage | None] = relationship()
