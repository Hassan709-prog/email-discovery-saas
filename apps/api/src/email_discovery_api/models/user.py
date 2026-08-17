"""User domain model for user accounts and security identities."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import TIMESTAMP, CheckConstraint, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from email_discovery_api.models.base import Base
from email_discovery_api.models.enums import UserStatus
from email_discovery_api.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from email_discovery_api.models.membership import Membership


class User(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """User identity entity."""

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "status IN ('ACTIVE', 'SUSPENDED', 'DELETED')",
            name="ck_users_status",
        ),
        CheckConstraint(
            "auth_version >= 1",
            name="ck_users_auth_version",
        ),
    )

    email: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_email: Mapped[str] = mapped_column(
        String(255), nullable=False, unique=True, index=True
    )
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default=UserStatus.ACTIVE.value,
    )
    email_verified_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    last_login_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    auth_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )

    # Relationships
    memberships: Mapped[list[Membership]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
