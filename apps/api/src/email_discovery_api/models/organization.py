"""Organization domain model for multi-tenant account isolation."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from email_discovery_api.models.base import Base
from email_discovery_api.models.enums import OrganizationStatus
from email_discovery_api.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from email_discovery_api.models.membership import Membership
    from email_discovery_api.models.scan_job import ScanJob


class Organization(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Organization entity serving as the primary tenant boundary."""

    __tablename__ = "organizations"
    __table_args__ = (
        CheckConstraint(
            "status IN ('ACTIVE', 'SUSPENDED')",
            name="ck_organizations_status",
        ),
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default=OrganizationStatus.ACTIVE.value,
    )

    # Relationships
    memberships: Mapped[list[Membership]] = relationship(
        back_populates="organization",
        cascade="all, delete-orphan",
    )
    scan_jobs: Mapped[list[ScanJob]] = relationship(
        back_populates="organization",
    )
