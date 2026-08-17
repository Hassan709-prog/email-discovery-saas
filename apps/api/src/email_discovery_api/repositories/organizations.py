"""Organization and membership access repository."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from email_discovery_api.models.enums import MembershipStatus, OrganizationStatus
from email_discovery_api.models.membership import Membership
from email_discovery_api.models.organization import Organization


class OrganizationAccessRepository:
    """Repository for tenant existence, locking, and membership verification."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_active_organization_for_update(
        self, organization_id: UUID
    ) -> Organization | None:
        """Fetch active organization row with a PostgreSQL row lock (FOR UPDATE)."""
        stmt = (
            select(Organization)
            .where(
                Organization.id == organization_id,
                Organization.status == OrganizationStatus.ACTIVE.value,
            )
            .with_for_update()
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_active_membership(
        self, organization_id: UUID, user_id: UUID
    ) -> Membership | None:
        """Fetch active user membership for organization tenant verification."""
        stmt = select(Membership).where(
            Membership.organization_id == organization_id,
            Membership.user_id == user_id,
            Membership.status == MembershipStatus.ACTIVE.value,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
