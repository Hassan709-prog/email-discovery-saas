"""Idempotency-Key header validation dependency."""

from __future__ import annotations

import re

from fastapi import Header, HTTPException, status

IDEMPOTENCY_KEY_REGEX = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


def validate_idempotency_key(
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
) -> str | None:
    """Validate optional Idempotency-Key header value."""
    if idempotency_key is None:
        return None

    if not IDEMPOTENCY_KEY_REGEX.match(idempotency_key):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Idempotency-Key must contain 1-128 characters matching pattern "
                "^[A-Za-z0-9._:-]{1,128}$"
            ),
        )

    return idempotency_key
