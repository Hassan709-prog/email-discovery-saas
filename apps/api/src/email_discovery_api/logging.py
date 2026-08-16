"""Structured logging setup and request-ID middleware."""

import logging
import re
import sys
import time
import uuid
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

# Conservative alphanumeric, underscore, hyphen request ID pattern (1-128 chars)
REQUEST_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_\-]{1,128}$")


def is_valid_request_id(request_id: str | None) -> bool:
    """Validate request ID against a conservative character set and length boundary."""
    if not request_id:
        return False
    return bool(REQUEST_ID_PATTERN.match(request_id))


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Middleware attaching valid caller X-Request-ID or generating a new UUID."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        incoming_id = request.headers.get("X-Request-ID")
        if is_valid_request_id(incoming_id):
            request_id = incoming_id.strip()  # pyright: ignore[reportOptionalMemberAccess]
        else:
            request_id = str(uuid.uuid4())

        request.state.request_id = request_id

        start_time = time.monotonic()
        try:
            response = await call_next(request)
        except Exception:
            # Duration calculation and logger error entry on unhandled exception
            duration_ms = (time.monotonic() - start_time) * 1000.0
            logging.getLogger("email_discovery_api").error(
                "Request failed: method=%s path=%s duration_ms=%.2f request_id=%s",
                request.method,
                request.url.path,
                duration_ms,
                request_id,
            )
            raise

        duration_ms = (time.monotonic() - start_time) * 1000.0
        response.headers["X-Request-ID"] = request_id

        logging.getLogger("email_discovery_api").info(
            "Request finished: method=%s path=%s status=%d duration_ms=%.2f request_id=%s",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
            request_id,
        )

        return response


def setup_logging(log_level: str = "INFO") -> None:
    """Configure structured logging output for the application."""
    numeric_level = getattr(logging, log_level.upper(), logging.INFO)
    logging.basicConfig(
        level=numeric_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
        force=True,
    )
