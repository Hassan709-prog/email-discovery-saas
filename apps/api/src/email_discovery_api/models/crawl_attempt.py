"""CrawlAttempt domain model representing a single scan execution attempt for a URL."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

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
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from email_discovery_api.models.base import Base
from email_discovery_api.models.mixins import UUIDPrimaryKeyMixin, utc_now

if TYPE_CHECKING:
    from email_discovery_api.models.crawled_page import CrawledPage
    from email_discovery_api.models.scan_url import ScanURL


class CrawlAttempt(Base, UUIDPrimaryKeyMixin):
    """CrawlAttempt entity for recording individual URL scan attempt diagnostic execution data.

    ForeignKey Deletion Behavior:
        scan_url_id uses ON DELETE CASCADE to clean up attempt history if a ScanURL is deleted.
    """

    __tablename__ = "crawl_attempts"
    __table_args__ = (
        UniqueConstraint(
            "scan_url_id", "attempt_number", name="uq_crawl_attempts_scan_url_attempt"
        ),
        CheckConstraint("attempt_number >= 1", name="ck_crawl_attempts_attempt_number"),
        CheckConstraint(
            "elapsed_seconds IS NULL OR elapsed_seconds >= 0.0",
            name="ck_crawl_attempts_elapsed_seconds",
        ),
        CheckConstraint(
            "status_code IS NULL OR (status_code >= 100 AND status_code <= 599)",
            name="ck_crawl_attempts_status_code",
        ),
        CheckConstraint(
            "result_checksum ~ '^[0-9a-f]{64}$'",
            name="ck_crawl_attempts_result_checksum_hex",
        ),
        CheckConstraint(
            "dns_duration_seconds IS NULL OR dns_duration_seconds >= 0.0",
            name="ck_crawl_attempts_dns_duration_nonnegative",
        ),
        CheckConstraint(
            "gate_wait_seconds IS NULL OR gate_wait_seconds >= 0.0",
            name="ck_crawl_attempts_gate_wait_nonnegative",
        ),
        CheckConstraint(
            "robots_duration_seconds IS NULL OR robots_duration_seconds >= 0.0",
            name="ck_crawl_attempts_robots_duration_nonnegative",
        ),
        CheckConstraint(
            "http_duration_seconds IS NULL OR http_duration_seconds >= 0.0",
            name="ck_crawl_attempts_http_duration_nonnegative",
        ),
        CheckConstraint(
            "parse_duration_seconds IS NULL OR parse_duration_seconds >= 0.0",
            name="ck_crawl_attempts_parse_duration_nonnegative",
        ),
        Index("ix_crawl_attempts_scan_url_created", "scan_url_id", "created_at"),
        Index("ix_crawl_attempts_outcome_created", "outcome", "created_at"),
    )

    scan_url_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("scan_urls.id", ondelete="CASCADE"),
        nullable=False,
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    outcome: Mapped[str] = mapped_column(String(50), nullable=False)
    retryable: Mapped[bool] = mapped_column(nullable=False, default=False)
    requested_url: Mapped[str] = mapped_column(Text, nullable=False)
    final_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    redirect_history: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)
    connection_attempts: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=utc_now,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
    )
    elapsed_seconds: Mapped[float | None] = mapped_column(nullable=True)
    dns_duration_seconds: Mapped[float | None] = mapped_column(nullable=True)
    gate_wait_seconds: Mapped[float | None] = mapped_column(nullable=True)
    robots_duration_seconds: Mapped[float | None] = mapped_column(nullable=True)
    http_duration_seconds: Mapped[float | None] = mapped_column(nullable=True)
    parse_duration_seconds: Mapped[float | None] = mapped_column(nullable=True)
    result_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=utc_now,
    )

    # Relationships
    scan_url: Mapped[ScanURL] = relationship()
    crawled_pages: Mapped[list[CrawledPage]] = relationship(
        back_populates="crawl_attempt",
        cascade="all, delete-orphan",
    )
