"""Injectable retry backoff policy with strict validation."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RetryBackoffPolicy:
    """Bounded exponential backoff policy for transient URL crawl retries."""

    base_delay_seconds: float = 5.0
    backoff_factor: float = 2.0
    max_delay_seconds: float = 3600.0

    def __post_init__(self) -> None:
        if (
            not math.isfinite(self.base_delay_seconds)
            or not math.isfinite(self.backoff_factor)
            or not math.isfinite(self.max_delay_seconds)
        ):
            raise ValueError("Retry backoff policy parameters must be finite numeric values.")
        if self.base_delay_seconds <= 0:
            raise ValueError("base_delay_seconds must be greater than zero.")
        if self.backoff_factor < 1.0:
            raise ValueError("backoff_factor must be at least 1.0.")
        if self.max_delay_seconds < self.base_delay_seconds:
            raise ValueError(
                "max_delay_seconds must be greater than or equal to base_delay_seconds."
            )

    def compute_delay_seconds(self, attempt_number: int) -> float:
        """Compute bounded exponential backoff delay for given attempt_number (1-indexed)."""
        if attempt_number <= 1:
            raw_delay = self.base_delay_seconds
        else:
            exponent = attempt_number - 1
            raw_delay = self.base_delay_seconds * (self.backoff_factor**exponent)

        if not math.isfinite(raw_delay) or raw_delay > self.max_delay_seconds:
            return self.max_delay_seconds
        return max(self.base_delay_seconds, float(raw_delay))
