"""EmailEvidence domain model linking an email finding to specific page evidence."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    TIMESTAMP,
    CheckConstraint,
    Float,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from email_discovery_api.models.base import Base
from email_discovery_api.models.mixins import UUIDPrimaryKeyMixin, utc_now

if TYPE_CHECKING:
    from email_discovery_api.models.crawled_page import CrawledPage
    from email_discovery_api.models.email_finding import EmailFinding


class EmailEvidence(Base, UUIDPrimaryKeyMixin):
    """EmailEvidence entity for storing evidence context of where a canonical email was found.

    Privacy & Security Note:
        Full page HTML is NEVER stored. Raw candidates and evidence snippets are length-bounded
        and sanitized (control characters removed).
    """

    __tablename__ = "email_evidence"
    __table_args__ = (
        UniqueConstraint(
            "email_finding_id",
            "crawled_page_id",
            "source_type",
            "candidate_hash",
            name="uq_email_evidence_finding_page_source_hash",
        ),
        CheckConstraint(
            "confidence >= 0.0 AND confidence <= 1.0",
            name="ck_email_evidence_confidence",
        ),
        CheckConstraint(
            "candidate_hash ~ '^[0-9a-f]{64}$'",
            name="ck_email_evidence_candidate_hash_hex",
        ),
    )

    email_finding_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("email_findings.id", ondelete="CASCADE"),
        nullable=False,
    )
    crawled_page_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("crawled_pages.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_type: Mapped[str] = mapped_column(String(50), nullable=False)
    raw_candidate: Mapped[str | None] = mapped_column(String(255), nullable=True)
    snippet: Mapped[str | None] = mapped_column(String(255), nullable=True)
    page_url: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    candidate_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=utc_now,
    )

    # Relationships
    email_finding: Mapped[EmailFinding] = relationship(back_populates="evidence_items")
    crawled_page: Mapped[CrawledPage] = relationship()
