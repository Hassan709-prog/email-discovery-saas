"""CrawledPage domain model representing an HTML page processed during a crawl attempt."""

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
from email_discovery_api.models.mixins import UUIDPrimaryKeyMixin, utc_now

if TYPE_CHECKING:
    from email_discovery_api.models.crawl_attempt import CrawlAttempt
    from email_discovery_api.models.scan_url import ScanURL


class CrawledPage(Base, UUIDPrimaryKeyMixin):
    """CrawledPage entity for recording individual processed HTML page metadata.

    Privacy & Security Note:
        Full HTML response bodies are NEVER stored. Only non-sensitive metadata, score,
        and sanitized counts are persisted.
    """

    __tablename__ = "crawled_pages"
    __table_args__ = (
        UniqueConstraint("crawl_attempt_id", "normalized_url", name="uq_crawled_pages_attempt_url"),
        CheckConstraint("depth >= 0", name="ck_crawled_pages_depth"),
        CheckConstraint(
            "page_score >= -100 AND page_score <= 1000", name="ck_crawled_pages_page_score"
        ),
        CheckConstraint(
            "status_code IS NULL OR (status_code >= 100 AND status_code <= 599)",
            name="ck_crawled_pages_status_code",
        ),
        CheckConstraint(
            "links_discovered_count >= 0 AND emails_found_count >= 0",
            name="ck_crawled_pages_counts",
        ),
        CheckConstraint(
            "content_sha256 IS NULL OR content_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_crawled_pages_content_sha256_hex",
        ),
        Index("ix_crawled_pages_scan_url", "scan_url_id"),
        Index("ix_crawled_pages_scan_url_normalized", "scan_url_id", "normalized_url"),
        Index("ix_crawled_pages_final_url", "final_url"),
    )

    crawl_attempt_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("crawl_attempts.id", ondelete="CASCADE"),
        nullable=False,
    )
    scan_url_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("scan_urls.id", ondelete="CASCADE"),
        nullable=False,
    )
    normalized_url: Mapped[str] = mapped_column(Text, nullable=False)
    final_url: Mapped[str] = mapped_column(Text, nullable=False)
    depth: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    outcome: Mapped[str] = mapped_column(String(50), nullable=False)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    content_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    content_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    page_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ranking_version: Mapped[str] = mapped_column(String(50), nullable=False)
    robots_decision: Mapped[str | None] = mapped_column(String(50), nullable=True)
    links_discovered_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    emails_found_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fetched_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=utc_now,
    )

    # Relationships
    crawl_attempt: Mapped[CrawlAttempt] = relationship(back_populates="crawled_pages")
    scan_url: Mapped[ScanURL] = relationship()
