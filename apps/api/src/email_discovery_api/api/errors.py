"""HTTP error response envelope formatting and exception handlers."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, cast

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from email_discovery_api.services.errors import ServiceError, ServiceErrorCode

logger = logging.getLogger("email_discovery_api.api.errors")

SERVICE_ERROR_STATUS_MAP: dict[ServiceErrorCode, int] = {
    ServiceErrorCode.USER_NOT_AUTHORIZED: status.HTTP_403_FORBIDDEN,
    ServiceErrorCode.ORGANIZATION_NOT_FOUND: status.HTTP_404_NOT_FOUND,
    ServiceErrorCode.JOB_NOT_FOUND: status.HTTP_404_NOT_FOUND,
    ServiceErrorCode.IDEMPOTENCY_CONFLICT: status.HTTP_409_CONFLICT,
    ServiceErrorCode.INVALID_STATE_TRANSITION: status.HTTP_409_CONFLICT,
    ServiceErrorCode.ACTIVE_JOB_LIMIT_EXCEEDED: status.HTTP_429_TOO_MANY_REQUESTS,
    ServiceErrorCode.INPUT_LIMIT_EXCEEDED: status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
    ServiceErrorCode.INPUT_TOO_LONG: status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
    ServiceErrorCode.CONFIGURATION_TOO_LARGE: status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
}

HTTP_STATUS_CODE_MAP: dict[int, str] = {
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    409: "CONFLICT",
    413: "PAYLOAD_TOO_LARGE",
    422: "UNPROCESSABLE_ENTITY",
    429: "TOO_MANY_REQUESTS",
    500: "INTERNAL_SERVER_ERROR",
}


def build_error_envelope(
    code: str, message: str, request_id: str | None
) -> dict[str, dict[str, Any]]:
    """Construct standard JSON error envelope dictionary."""
    return {
        "error": {
            "code": code,
            "message": message,
            "request_id": request_id,
        }
    }


def get_request_id(request: Request) -> str | None:
    """Extract request ID set by RequestIdMiddleware."""
    return getattr(request.state, "request_id", None)


async def service_error_handler(request: Request, exc: ServiceError) -> JSONResponse:
    """Handle ServiceError exceptions by mapping to HTTP status codes and error envelopes."""
    request_id = get_request_id(request)
    status_code = SERVICE_ERROR_STATUS_MAP.get(exc.code, status.HTTP_500_INTERNAL_SERVER_ERROR)
    headers: dict[str, str] = {}
    if request_id:
        headers["X-Request-ID"] = request_id

    content = build_error_envelope(exc.code.value, exc.message, request_id)
    return JSONResponse(status_code=status_code, content=content, headers=headers)


async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """Handle FastAPI HTTPException instances with standard error envelopes."""
    request_id = get_request_id(request)
    code = HTTP_STATUS_CODE_MAP.get(exc.status_code, "ERROR")
    message = str(exc.detail) if exc.detail else "An HTTP error occurred."

    headers: dict[str, str] = dict(exc.headers or {})
    if request_id:
        headers["X-Request-ID"] = request_id

    content = build_error_envelope(code, message, request_id)
    return JSONResponse(status_code=exc.status_code, content=content, headers=headers)


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Handle Pydantic RequestValidationError instances with 422 error envelopes."""
    request_id = get_request_id(request)
    headers: dict[str, str] = {}
    if request_id:
        headers["X-Request-ID"] = request_id

    content = build_error_envelope(
        "UNPROCESSABLE_ENTITY",
        "The request body or parameters failed validation.",
        request_id,
    )
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=content,
        headers=headers,
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Handle unhandled exceptions with sanitized 500 envelopes while logging tracebacks."""
    if isinstance(exc, asyncio.CancelledError):
        raise exc

    request_id = get_request_id(request)
    logger.exception("Unhandled server exception occurred for request %s", request_id)

    headers: dict[str, str] = {}
    if request_id:
        headers["X-Request-ID"] = request_id

    content = build_error_envelope(
        "INTERNAL_SERVER_ERROR",
        "An unexpected error occurred.",
        request_id,
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=content,
        headers=headers,
    )


def register_error_handlers(app: FastAPI) -> None:
    """Register all exception handlers on the FastAPI application instance."""
    app.add_exception_handler(ServiceError, cast(Any, service_error_handler))
    app.add_exception_handler(HTTPException, cast(Any, http_exception_handler))
    app.add_exception_handler(RequestValidationError, cast(Any, validation_exception_handler))
    app.add_exception_handler(Exception, unhandled_exception_handler)
