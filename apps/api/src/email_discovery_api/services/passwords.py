"""Argon2id password hashing, verification, and bounded execution service."""

from __future__ import annotations

import asyncio

from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher

from email_discovery_api.config import Settings, get_settings


class PasswordPolicyError(Exception):
    """Raised when password violates length requirements."""


class PasswordService:
    """Argon2id password hashing service with off-event-loop concurrency controls."""

    def __init__(
        self, settings: Settings | None = None, hasher: PasswordHash | None = None
    ) -> None:
        self._settings = settings or get_settings()
        self._min_len = self._settings.auth_password_min_length
        self._max_len = self._settings.auth_password_max_length
        self._semaphore = asyncio.Semaphore(self._settings.auth_hash_concurrency_limit)

        if hasher is not None:
            self._hasher = hasher
        else:
            # Standard OWASP compliant Argon2id settings: 64MB memory, 3 iterations, 4 parallelism
            argon2_hasher = Argon2Hasher(
                memory_cost=65536,
                time_cost=3,
                parallelism=4,
            )
            self._hasher = PasswordHash((argon2_hasher,))

        # Precompute dummy hash for unknown email timing mitigation
        self._dummy_hash = self._hasher.hash("dummy_password_for_timing_mitigation_123")

    def validate_password_policy(self, password: str) -> None:
        """Validate password against configured length policy without trimming."""
        if len(password) < self._min_len or len(password) > self._max_len:
            raise PasswordPolicyError(
                f"Password must be between {self._min_len} and {self._max_len} characters long."
            )

    async def hash_password(self, password: str) -> str:
        """Hash plain password off event loop with concurrency bounds."""
        self.validate_password_policy(password)
        async with self._semaphore:
            return await asyncio.to_thread(self._hasher.hash, password)

    async def verify_password(self, password: str, hash_str: str | None) -> bool:
        """Verify plain password against stored hash off event loop with concurrency bounds."""
        target_hash = hash_str if hash_str else self._dummy_hash
        async with self._semaphore:
            valid, _ = await asyncio.to_thread(
                self._hasher.verify_and_update, password, target_hash
            )

        if not hash_str:
            return False
        return valid

    def needs_rehash(self, hash_str: str) -> bool:
        """Check if stored hash needs rehashing to meet current policy."""
        try:
            func = getattr(self._hasher, "check_needs_rehash", None)
            if callable(func):
                return bool(func(hash_str))
            _, new_hash = self._hasher.verify_and_update("", hash_str)
            return new_hash is not None
        except Exception:
            return False

    async def verify_and_rehash(self, password: str, hash_str: str) -> tuple[bool, str | None]:
        """Verify password and return (is_valid, new_hash_if_needed)."""
        async with self._semaphore:
            valid, updated_hash = await asyncio.to_thread(
                self._hasher.verify_and_update, password, hash_str
            )
        return valid, updated_hash
