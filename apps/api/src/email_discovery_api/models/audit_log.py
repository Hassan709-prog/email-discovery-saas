"""AuditLog domain model for security and administrative audit trail."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import TIMESTAMP, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from email_discovery_api.models.base import Base
from email_discovery_api.models.mixins import UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from email_discovery_api.models.organization import Organization
    from email_discovery_api.models.user import User


def utc_now() -> datetime:
    """Return timezone-aware current UTC datetime."""
    return datetime.now(UTC)


class AuditLog(Base, UUIDPrimaryKeyMixin):
    """AuditLog entity for administrator and security audit trail.

    Reserved Name Note:
        SQLAlchemy DeclarativeBase reserves the attribute name 'metadata'.
        The Python attribute is named 'metadata_' and mapped explicitly to DB column 'metadata'.

    Security Invariant:
        Never log passwords, bearer tokens, auth headers, secret keys, or raw request bodies.
        organization_id and actor_user_id use ON DELETE SET NULL to preserve security records.
    """

    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_org_created", "organization_id", "created_at"),
        Index("ix_audit_logs_actor_created", "actor_user_id", "created_at"),
        Index("ix_audit_logs_target", "target_type", "target_id"),
    )

    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="SET NULL"),
        nullable=True,
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    target_type: Mapped[str] = mapped_column(String(100), nullable=False)
    target_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    before_values: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    after_values: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
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
    organization: Mapped[Organization | None] = relationship()
    actor_user: Mapped[User | None] = relationship()
