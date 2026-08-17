"""Tenant-scoped user, organization, and membership data access repository."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from email_discovery_api.models.enums import (
    MembershipRole,
    MembershipStatus,
    OrganizationStatus,
    UserStatus,
)
from email_discovery_api.models.membership import Membership
from email_discovery_api.models.organization import Organization
from email_discovery_api.models.user import User


class UserRepository:
    """Repository managing User, Organization, and Membership persistent identity entities."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_email(self, normalized_email: str) -> User | None:
        """Fetch user by normalized email address."""
        stmt = select(User).where(User.normalized_email == normalized_email)
        res = await self._session.execute(stmt)
        return res.scalar_one_or_none()

    async def get_by_id(self, user_id: UUID) -> User | None:
        """Fetch user by UUID primary key."""
        stmt = select(User).where(User.id == user_id)
        res = await self._session.execute(stmt)
        return res.scalar_one_or_none()

    async def create_user(
        self,
        email: str,
        normalized_email: str,
        password_hash: str,
        display_name: str | None = None,
        status: str = UserStatus.ACTIVE.value,
    ) -> User:
        """Create new User identity record without committing."""
        user = User(
            email=email,
            normalized_email=normalized_email,
            password_hash=password_hash,
            display_name=display_name,
            status=status,
            auth_version=1,
        )
        self._session.add(user)
        await self._session.flush()
        return user

    async def update_last_login(self, user_id: UUID, login_time: datetime) -> None:
        """Update last_login_at timestamp for a user without committing."""
        stmt = update(User).where(User.id == user_id).values(last_login_at=login_time)
        await self._session.execute(stmt)

    async def increment_auth_version(self, user_id: UUID) -> int:
        """Atomically increment User.auth_version by 1 without committing."""
        stmt = (
            update(User)
            .where(User.id == user_id)
            .values(auth_version=User.auth_version + 1)
            .returning(User.auth_version)
        )
        res = await self._session.execute(stmt)
        new_version = res.scalar_one()
        return new_version

    async def create_organization(
        self,
        name: str,
        slug: str,
        status: str = OrganizationStatus.ACTIVE.value,
    ) -> Organization:
        """Create new Organization tenant record without committing."""
        org = Organization(
            name=name,
            slug=slug,
            status=status,
        )
        self._session.add(org)
        await self._session.flush()
        return org

    async def create_membership(
        self,
        user_id: UUID,
        organization_id: UUID,
        role: str = MembershipRole.OWNER.value,
        status: str = MembershipStatus.ACTIVE.value,
    ) -> Membership:
        """Create new Membership association without committing."""
        membership = Membership(
            user_id=user_id,
            organization_id=organization_id,
            role=role,
            status=status,
        )
        self._session.add(membership)
        await self._session.flush()
        return membership

    async def get_user_active_memberships(
        self, user_id: UUID
    ) -> list[tuple[Membership, Organization]]:
        """Fetch all ACTIVE memberships and associated ACTIVE organizations for a user."""
        stmt = (
            select(Membership, Organization)
            .join(Organization, Membership.organization_id == Organization.id)
            .where(
                Membership.user_id == user_id,
                Membership.status == MembershipStatus.ACTIVE.value,
                Organization.status == OrganizationStatus.ACTIVE.value,
            )
        )
        res = await self._session.execute(stmt)
        return list(res.tuples().all())

    async def get_active_membership(
        self, user_id: UUID, organization_id: UUID
    ) -> tuple[Membership, Organization] | None:
        """Fetch active membership and organization for a specific user and tenant ID."""
        stmt = (
            select(Membership, Organization)
            .join(Organization, Membership.organization_id == Organization.id)
            .where(
                Membership.user_id == user_id,
                Membership.organization_id == organization_id,
                Membership.status == MembershipStatus.ACTIVE.value,
                Organization.status == OrganizationStatus.ACTIVE.value,
            )
        )
        res = await self._session.execute(stmt)
        result = res.tuples().first()
        return result if result else None
