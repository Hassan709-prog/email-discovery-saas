"""Unit tests for Argon2id password service, policy, and off-event-loop execution."""

import asyncio

import pytest
from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher

from email_discovery_api.services.passwords import PasswordPolicyError, PasswordService


@pytest.fixture
def fast_password_service() -> PasswordService:
    """PasswordService with fast low-cost Argon2 parameters for test speed."""
    fast_hasher = PasswordHash((Argon2Hasher(memory_cost=1024, time_cost=1, parallelism=1),))
    return PasswordService(hasher=fast_hasher)


@pytest.mark.anyio
async def test_password_hash_and_verify(fast_password_service: PasswordService) -> None:
    """Verify password hashing, correct verification, and wrong password rejection."""
    password = "CorrectPassword123!"
    hashed = await fast_password_service.hash_password(password)

    assert hashed.startswith("$argon2id$")
    assert await fast_password_service.verify_password(password, hashed) is True
    assert await fast_password_service.verify_password("WrongPassword123!", hashed) is False


@pytest.mark.anyio
async def test_password_policy_length_validation(fast_password_service: PasswordService) -> None:
    """Verify password length boundaries (12-128 characters) are strictly enforced."""
    with pytest.raises(PasswordPolicyError):
        await fast_password_service.hash_password("Short1!")

    with pytest.raises(PasswordPolicyError):
        await fast_password_service.hash_password("a" * 129)

    valid_12 = await fast_password_service.hash_password("TwelveChar1!")
    assert valid_12 is not None


@pytest.mark.anyio
async def test_dummy_hash_verification_for_missing_user(
    fast_password_service: PasswordService,
) -> None:
    """Verify missing user path executes dummy hash check and returns False."""
    is_valid = await fast_password_service.verify_password("AnyPassword123!", None)
    assert is_valid is False


@pytest.mark.anyio
async def test_bounded_concurrency_execution(fast_password_service: PasswordService) -> None:
    """Verify simultaneous password operations execute cleanly through bounded executor."""
    passwords = [f"PasswordVariant{i}!" for i in range(5)]
    hashes = await asyncio.gather(*(fast_password_service.hash_password(p) for p in passwords))

    assert len(hashes) == 5
    verifications = await asyncio.gather(
        *(
            fast_password_service.verify_password(p, h)
            for p, h in zip(passwords, hashes, strict=True)
        )
    )
    assert all(verifications)
