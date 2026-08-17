"""Stable service error codes and exceptions for application logic failures."""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class ServiceErrorCode(StrEnum):
    """Stable domain and service error codes."""

    INPUT_LIMIT_EXCEEDED = "INPUT_LIMIT_EXCEEDED"
    INPUT_TOO_LONG = "INPUT_TOO_LONG"
    ACTIVE_JOB_LIMIT_EXCEEDED = "ACTIVE_JOB_LIMIT_EXCEEDED"
    CONFIGURATION_TOO_LARGE = "CONFIGURATION_TOO_LARGE"
    ORGANIZATION_NOT_FOUND = "ORGANIZATION_NOT_FOUND"
    USER_NOT_AUTHORIZED = "USER_NOT_AUTHORIZED"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    JOB_NOT_FOUND = "JOB_NOT_FOUND"
    INVALID_STATE_TRANSITION = "INVALID_STATE_TRANSITION"


class ServiceError(Exception):
    """Base domain service exception carrying a stable error code and optional details."""

    def __init__(
        self,
        code: ServiceErrorCode,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    def __repr__(self) -> str:
        return f"ServiceError(code={self.code.value!r}, message={self.message!r})"
