"""RefreshSession persistence repository with FOR UPDATE row locking capabilities."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from email_discovery_api.models.enums import RefreshSessionStatus
from email_discovery_api.models.refresh_session import RefreshSession


class RefreshSessionRepository:
    """Repository managing RefreshSession entity persistence and status transitions."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_token_hash_for_update(self, token_hash: str) -> RefreshSession | None:
        """Fetch RefreshSession by token_hash with a pessimistic FOR UPDATE row lock."""
        stmt = (
            select(RefreshSession).where(RefreshSession.token_hash == token_hash).with_for_update()
        )
        res = await self._session.execute(stmt)
        return res.scalar_one_or_none()

    async def create_session(
        self,
        user_id: UUID,
        organization_id: UUID,
        family_id: UUID,
        token_hash: str,
        csrf_token_hash: str,
        auth_version: int,
        expires_at: datetime,
        parent_session_id: UUID | None = None,
    ) -> RefreshSession:
        """Create new RefreshSession entity without committing."""
        session_obj = RefreshSession(
            user_id=user_id,
            organization_id=organization_id,
            family_id=family_id,
            token_hash=token_hash,
            csrf_token_hash=csrf_token_hash,
            status=RefreshSessionStatus.ACTIVE.value,
            auth_version=auth_version,
            expires_at=expires_at,
            parent_session_id=parent_session_id,
        )
        self._session.add(session_obj)
        await self._session.flush()
        return session_obj

    async def rotate_session(
        self,
        old_session: RefreshSession,
        new_session: RefreshSession,
        used_at: datetime,
    ) -> None:
        """Update old session state to ROTATED and link replacement session without committing."""
        old_session.status = RefreshSessionStatus.ROTATED.value
        old_session.used_at = used_at
        old_session.replaced_by_session_id = new_session.id
        new_session.parent_session_id = old_session.id
        await self._session.flush()

    async def revoke_family(self, family_id: UUID, revoked_at: datetime) -> int:
        """Revoke entire token family as COMPROMISED without committing."""
        stmt = (
            update(RefreshSession)
            .where(RefreshSession.family_id == family_id)
            .values(
                status=RefreshSessionStatus.COMPROMISED.value,
                revoked_at=revoked_at,
            )
        )
        res = await self._session.execute(stmt)
        return int(getattr(res, "rowcount", 0))

    async def revoke_session(self, session_id: UUID, revoked_at: datetime) -> None:
        """Revoke a single session without committing."""
        stmt = (
            update(RefreshSession)
            .where(RefreshSession.id == session_id)
            .values(
                status=RefreshSessionStatus.REVOKED.value,
                revoked_at=revoked_at,
            )
        )
        await self._session.execute(stmt)

    async def revoke_all_user_sessions(self, user_id: UUID, revoked_at: datetime) -> int:
        """Revoke all active/rotated sessions for a user without committing."""
        stmt = (
            update(RefreshSession)
            .where(
                RefreshSession.user_id == user_id,
                RefreshSession.status.in_(
                    [
                        RefreshSessionStatus.ACTIVE.value,
                        RefreshSessionStatus.ROTATED.value,
                    ]
                ),
            )
            .values(
                status=RefreshSessionStatus.REVOKED.value,
                revoked_at=revoked_at,
            )
        )
        res = await self._session.execute(stmt)
        return int(getattr(res, "rowcount", 0))
