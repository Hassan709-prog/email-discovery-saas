"""Deterministic URL-safe base64 JSON pagination cursor encoding and decoding utilities."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from fastapi import HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, ValidationError

MAX_CURSOR_BYTES = 512


class CursorPayload(BaseModel):
    """Strict schema for cursor encoding and decoding."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1] = Field(..., description="Cursor format version number")
    resource: Literal["jobs", "urls", "events"] = Field(..., description="Resource entity type")
    values: list[Any] = Field(..., min_length=1, max_length=5)


def encode_cursor(resource: Literal["jobs", "urls", "events"], values: list[Any]) -> str:
    """Encode cursor values into a deterministic URL-safe base64 JSON string."""
    payload = CursorPayload(version=1, resource=resource, values=values)
    raw_json = payload.model_dump_json(by_alias=True)
    return base64.urlsafe_b64encode(raw_json.encode("utf-8")).decode("ascii").rstrip("=")


def decode_cursor(
    cursor_str: str, expected_resource: Literal["jobs", "urls", "events"]
) -> list[Any]:
    """Decode and validate a URL-safe base64 cursor for a specific expected resource type."""
    if not cursor_str or len(cursor_str.encode("utf-8")) > MAX_CURSOR_BYTES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cursor exceeds maximum allowed size or is empty.",
        )

    # Re-pad base64 string if trimmed
    padded = cursor_str + "=" * (-len(cursor_str) % 4)

    try:
        raw_bytes = base64.urlsafe_b64decode(padded.encode("ascii"))
        if len(raw_bytes) > MAX_CURSOR_BYTES:
            raise ValueError("Decoded cursor byte length exceeds maximum allowed size.")
        data = json.loads(raw_bytes.decode("utf-8"))
        payload = CursorPayload.model_validate(data)
    except (ValueError, TypeError, ValidationError) as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Malformed or invalid pagination cursor.",
        ) from err

    if payload.version != 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported cursor format version.",
        )

    if payload.resource != expected_resource:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Cursor resource mismatch: expected {expected_resource!r}, "
                f"got {payload.resource!r}."
            ),
        )

    return payload.values


def parse_job_cursor(cursor_str: str | None) -> tuple[datetime | None, UUID | None]:
    """Parse a job pagination cursor returning (cursor_created_at, cursor_id)."""
    if not cursor_str:
        return None, None
    values = decode_cursor(cursor_str, "jobs")
    if len(values) != 2:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid job cursor values length.",
        )
    try:
        created_at = datetime.fromisoformat(str(values[0])).astimezone(UTC)
        job_id = UUID(str(values[1]))
        return created_at, job_id
    except ValueError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid job cursor value format.",
        ) from err


def parse_url_cursor(cursor_str: str | None) -> tuple[int | None, UUID | None]:
    """Parse a URL pagination cursor returning (cursor_original_index, cursor_id)."""
    if not cursor_str:
        return None, None
    values = decode_cursor(cursor_str, "urls")
    if len(values) != 2:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid URL cursor values length.",
        )
    try:
        idx = int(values[0])
        url_id = UUID(str(values[1]))
        return idx, url_id
    except ValueError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid URL cursor value format.",
        ) from err


def parse_event_cursor(cursor_str: str | None) -> tuple[int | None, UUID | None]:
    """Parse an event pagination cursor returning (cursor_sequence_number, cursor_id)."""
    if not cursor_str:
        return None, None
    values = decode_cursor(cursor_str, "events")
    if len(values) != 2:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid event cursor values length.",
        )
    try:
        seq = int(values[0])
        event_id = UUID(str(values[1]))
        return seq, event_id
    except ValueError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid event cursor value format.",
        ) from err
