"""RefreshSession model representing rotated refresh tokens and CSRF bindings."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    TIMESTAMP,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from email_discovery_api.models.base import Base
from email_discovery_api.models.enums import RefreshSessionStatus
from email_discovery_api.models.mixins import UUIDPrimaryKeyMixin, utc_now

if TYPE_CHECKING:
    from email_discovery_api.models.organization import Organization
    from email_discovery_api.models.user import User


class RefreshSession(Base, UUIDPrimaryKeyMixin):
    """Secure refresh session with token rotation and CSRF binding."""

    __tablename__ = "refresh_sessions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('ACTIVE', 'ROTATED', 'REVOKED', 'COMPROMISED')",
            name="ck_refresh_sessions_status",
        ),
        CheckConstraint(
            "token_hash ~ '^[0-9a-f]{64}$'",
            name="ck_refresh_sessions_token_hash_hex",
        ),
        CheckConstraint(
            "csrf_token_hash ~ '^[0-9a-f]{64}$'",
            name="ck_refresh_sessions_csrf_token_hash_hex",
        ),
        CheckConstraint(
            "auth_version >= 1",
            name="ck_refresh_sessions_auth_version",
        ),
        CheckConstraint(
            "expires_at > created_at",
            name="ck_refresh_sessions_expires_at",
        ),
        CheckConstraint(
            "parent_session_id IS NULL OR parent_session_id != id",
            name="ck_refresh_sessions_parent_not_self",
        ),
        CheckConstraint(
            "replaced_by_session_id IS NULL OR replaced_by_session_id != id",
            name="ck_refresh_sessions_replaced_by_not_self",
        ),
        Index("ix_refresh_sessions_user_status", "user_id", "status"),
        Index("ix_refresh_sessions_family_status", "family_id", "status"),
        Index("ix_refresh_sessions_expires_at", "expires_at"),
    )

    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    organization_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    family_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    token_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
    )
    csrf_token_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default=RefreshSessionStatus.ACTIVE.value,
    )
    parent_session_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("refresh_sessions.id", ondelete="SET NULL"),
        nullable=True,
    )
    replaced_by_session_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("refresh_sessions.id", ondelete="SET NULL"),
        nullable=True,
    )
    auth_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
    )
    used_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=utc_now,
    )

    # Relationships
    user: Mapped[User] = relationship("User", foreign_keys=[user_id])
    organization: Mapped[Organization] = relationship(
        "Organization", foreign_keys=[organization_id]
    )
    parent_session: Mapped[RefreshSession | None] = relationship(
        "RefreshSession",
        remote_side="RefreshSession.id",
        foreign_keys=[parent_session_id],
        post_update=True,
    )
    replaced_by_session: Mapped[RefreshSession | None] = relationship(
        "RefreshSession",
        remote_side="RefreshSession.id",
        foreign_keys=[replaced_by_session_id],
        post_update=True,
    )
