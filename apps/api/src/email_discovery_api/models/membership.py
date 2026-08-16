"""Membership domain model linking Users to Organizations with roles."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from email_discovery_api.models.base import Base
from email_discovery_api.models.enums import MembershipRole, MembershipStatus
from email_discovery_api.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from email_discovery_api.models.organization import Organization
    from email_discovery_api.models.user import User


class Membership(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """User membership within an Organization establishing role authorization."""

    __tablename__ = "memberships"
    __table_args__ = (
        UniqueConstraint("organization_id", "user_id", name="uq_memberships_org_user"),
        CheckConstraint(
            "role IN ('OWNER', 'ADMIN', 'MEMBER', 'VIEWER')",
            name="ck_memberships_role",
        ),
        CheckConstraint(
            "status IN ('ACTIVE', 'INVITED', 'SUSPENDED')",
            name="ck_memberships_status",
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default=MembershipRole.MEMBER.value,
    )
    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default=MembershipStatus.ACTIVE.value,
    )

    # Relationships
    organization: Mapped[Organization] = relationship(back_populates="memberships")
    user: Mapped[User] = relationship(back_populates="memberships")
