"""Database engine, session factory, declarative base, and lifecycle management."""

import asyncio
from collections.abc import AsyncGenerator

from fastapi import Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from email_discovery_api.config import Settings


class Base(DeclarativeBase):
    """Declarative Base for application SQLAlchemy models."""

    pass


class DatabaseManager:
    """Application-owned database engine and session factory manager."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.engine: AsyncEngine = create_async_engine(
            settings.get_database_url_str(),
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_timeout=settings.db_pool_timeout,
            pool_recycle=settings.db_pool_recycle,
            future=True,
        )
        self.session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
            bind=self.engine,
            expire_on_commit=False,
            class_=AsyncSession,
        )

    async def close(self) -> None:
        """Dispose the SQLAlchemy engine connection pool on application shutdown."""
        await self.engine.dispose()

    async def check_health(self, timeout_seconds: float) -> bool:
        """Perform a bounded SELECT 1 readiness query returning True if PostgreSQL is reachable."""
        try:
            async with asyncio.timeout(timeout_seconds):
                async with self.session_factory() as session:
                    res = await session.execute(text("SELECT 1"))
                    val = res.scalar()
                    return val == 1
        except Exception:
            # Catch timeouts, connection errors, and DB exceptions without leaking details
            return False


async def get_db_session(request: Request) -> AsyncGenerator[AsyncSession]:
    """FastAPI dependency injecting an AsyncSession with session cleanup."""
    db_manager: DatabaseManager = request.app.state.db_manager
    async with db_manager.session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def get_identity_db_session(request: Request) -> AsyncGenerator[AsyncSession]:
    """FastAPI dependency injecting a separate AsyncSession for authentication lookup.

    Using a distinct dependency function provides a unique FastAPI dependency cache key,
    ensuring identity resolution runs on an independent AsyncSession from application
    service write transactions.
    """
    db_manager: DatabaseManager = request.app.state.db_manager
    async with db_manager.session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
