"""Injectable limit and validation policies for job creation and processing."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from email_discovery_api.services.errors import ServiceError, ServiceErrorCode


@dataclass(frozen=True)
class ScanCreationPolicy:
    """Configurable limits policy enforced prior to database row ingestion."""

    max_inputs_per_job: int = 10_000
    max_input_length: int = 2_048
    max_active_jobs_per_organization: int = 5
    max_configuration_json_bytes: int = 65_536

    def validate_pre_ingestion(
        self,
        inputs: list[str],
        configuration_snapshot: dict[str, Any],
    ) -> None:
        """Validate input list length, individual input character length, and configuration size.

        Must be called BEFORE normalizing inputs or constructing ORM objects.
        """
        if len(inputs) > self.max_inputs_per_job:
            raise ServiceError(
                ServiceErrorCode.INPUT_LIMIT_EXCEEDED,
                f"Job contains {len(inputs)} inputs, exceeding limit of {self.max_inputs_per_job}.",
                details={"input_count": len(inputs), "max_allowed": self.max_inputs_per_job},
            )

        for idx, raw in enumerate(inputs):
            if len(raw) > self.max_input_length:
                raise ServiceError(
                    ServiceErrorCode.INPUT_TOO_LONG,
                    f"Input at index {idx} exceeds maximum length of {self.max_input_length}.",
                    details={
                        "original_index": idx,
                        "length": len(raw),
                        "max_allowed": self.max_input_length,
                    },
                )

        try:
            config_bytes = len(json.dumps(configuration_snapshot).encode("utf-8"))
        except (TypeError, ValueError) as err:
            raise ServiceError(
                ServiceErrorCode.CONFIGURATION_TOO_LARGE,
                "Configuration snapshot is not JSON-serializable.",
            ) from err

        if config_bytes > self.max_configuration_json_bytes:
            raise ServiceError(
                ServiceErrorCode.CONFIGURATION_TOO_LARGE,
                f"Config size ({config_bytes}B) exceeds "
                f"limit ({self.max_configuration_json_bytes}B).",
                details={
                    "byte_size": config_bytes,
                    "max_allowed": self.max_configuration_json_bytes,
                },
            )
