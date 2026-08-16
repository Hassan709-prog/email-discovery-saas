"""JobEvent domain model for append-only audit trail of batch scan progress."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    TIMESTAMP,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from email_discovery_api.models.base import Base
from email_discovery_api.models.mixins import UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from email_discovery_api.models.scan_job import ScanJob
    from email_discovery_api.models.scan_url import ScanURL


def utc_now() -> datetime:
    """Return timezone-aware current UTC datetime."""
    return datetime.now(UTC)


class JobEvent(Base, UUIDPrimaryKeyMixin):
    """JobEvent entity for immutable event history of job execution.

    Immutability & Retention:
        - Append-only event store: repository code must never modify or delete events.
        - scan_url_id uses ON DELETE SET NULL so events survive ScanURL cleanup.
        - Deleting a ScanJob may cascade deletion of associated events.
    """

    __tablename__ = "job_events"
    __table_args__ = (
        UniqueConstraint("scan_job_id", "sequence_number", name="uq_job_events_job_seq"),
        Index("ix_job_events_job_created", "scan_job_id", "created_at"),
        CheckConstraint("sequence_number >= 1", name="ck_job_events_seq_positive"),
    )

    scan_job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("scan_jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    scan_url_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("scan_urls.id", ondelete="SET NULL"),
        nullable=True,
    )
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    # Relationships
    scan_job: Mapped[ScanJob] = relationship(back_populates="events")
    scan_url: Mapped[ScanURL | None] = relationship()
