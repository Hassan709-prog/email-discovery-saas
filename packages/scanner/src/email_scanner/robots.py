"""Robots.txt policy evaluator for scanner-core.

Fetches and evaluates robots.txt rules for target URLs with in-memory TTL
caching and deterministic clock support.
"""

import asyncio
import time
import urllib.robotparser
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from email_scanner.errors import (
    FetchOutcomeCode,
    RobotsDecisionCode,
    URLNormalizationError,
)
from email_scanner.fetching import AsyncHTTPFetcher
from email_scanner.models import (
    FetchConfig,
    FetchResult,
    NormalizedURL,
    RobotsDecision,
)
from email_scanner.normalization import normalize_url


@dataclass(slots=True)
class _CachedRobotsPolicy:
    policy_type: str  # "PARSED", "ALWAYS_ALLOW", "ALWAYS_DISALLOW", "TEMPORARY_FAILURE"
    parser: urllib.robotparser.RobotFileParser | None
    crawl_delay: float | None
    reason: str
    expires_at: float


def _extract_crawl_delay(
    parser: urllib.robotparser.RobotFileParser,
    body_text: str | None,
    token: str,
) -> float | None:
    delay = parser.crawl_delay(token)
    if delay is None:
        delay = parser.crawl_delay("*")
    if delay is not None:
        return float(delay)

    if not body_text:
        return None

    token_lower = token.lower()
    current_agents: list[str] = []
    matched_delay: float | None = None
    wildcard_delay: float | None = None
    in_user_agent_block = False

    for raw_line in body_text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split(":", 1)
        if len(parts) != 2:
            continue

        key = parts[0].strip().lower()
        val = parts[1].strip()

        if key == "user-agent":
            if not in_user_agent_block:
                current_agents = []
                in_user_agent_block = True
            current_agents.append(val.lower())
        elif key == "crawl-delay":
            in_user_agent_block = False
            try:
                parsed_val = float(val)
                if any(agent == token_lower for agent in current_agents):
                    matched_delay = parsed_val
                elif any(agent == "*" for agent in current_agents):
                    wildcard_delay = parsed_val
            except ValueError:
                pass
        else:
            in_user_agent_block = False

    if matched_delay is not None:
        return matched_delay
    return wildcard_delay


class RobotsPolicyEvaluator:
    """Evaluates robots.txt rules with TTL caching and single-flight misses."""

    def __init__(
        self,
        fetcher: AsyncHTTPFetcher | None = None,
        clock: Callable[[], float] | None = None,
        cache_ttl_seconds: float = 3600.0,
        temp_fail_ttl_seconds: float = 60.0,
        config: FetchConfig | None = None,
        cache_ttl: float | None = None,
    ) -> None:
        self._fetcher = fetcher or AsyncHTTPFetcher(config=config)
        self._clock = clock or time.monotonic
        self._cache_ttl = cache_ttl if cache_ttl is not None else cache_ttl_seconds
        self._temp_fail_ttl = temp_fail_ttl_seconds
        self._cache: dict[tuple[str, str], _CachedRobotsPolicy] = {}
        self._single_flight_locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._config = config or self._fetcher.config

    @property
    def config(self) -> FetchConfig:
        return self._config

    def _get_origin(self, url: NormalizedURL) -> str:
        if url.port:
            return f"{url.scheme}://{url.hostname}:{url.port}"
        return f"{url.scheme}://{url.hostname}"

    async def evaluate(
        self,
        url: str | NormalizedURL,
        user_agent_token: str | None = None,
        recorder: Any | None = None,
    ) -> RobotsDecision:
        """Evaluate robots.txt policy for a target URL asynchronously."""
        eval_start_t = self._clock()
        robots_fetch_elapsed = 0.0
        if isinstance(url, str):
            try:
                target_url = normalize_url(url)
            except URLNormalizationError as err:
                decision = RobotsDecision(
                    target_url=url,
                    decision=RobotsDecisionCode.TEMPORARY_FAILURE,
                    crawl_delay=None,
                    reason=f"Invalid target URL: {err}",
                )
                if recorder is not None:
                    dt = max(0.0, self._clock() - eval_start_t)
                    recorder.robots_evaluation_duration_seconds += dt
                return decision
        else:
            target_url = url

        token = user_agent_token or self._config.robots_user_agent_token
        origin = self._get_origin(target_url)
        cache_key = (origin, token)
        now = self._clock()

        cached_policy = self._cache.get(cache_key)
        if cached_policy is None or now >= cached_policy.expires_at:
            if cache_key not in self._single_flight_locks:
                self._single_flight_locks[cache_key] = asyncio.Lock()
            lock = self._single_flight_locks[cache_key]

            async with lock:
                now = self._clock()
                cached_policy = self._cache.get(cache_key)
                if cached_policy is None or now >= cached_policy.expires_at:
                    robots_url_str = f"{origin}/robots.txt"

                    r_fetch_start = self._clock()
                    robots_result = await self._fetcher.fetch(
                        robots_url_str,
                        allowed_content_types=(
                            "text/plain",
                            "text/robots.txt",
                            "text/html",
                            "application/xhtml+xml",
                        ),
                        recorder=recorder,
                    )
                    if recorder is not None:
                        dt = max(0.0, self._clock() - r_fetch_start)
                        robots_fetch_elapsed += dt
                        recorder.robots_fetch_duration_seconds += dt

                    cached_policy = self._build_policy(robots_result, token, now)
                    self._cache[cache_key] = cached_policy

        if cached_policy.crawl_delay is not None:
            self._fetcher.request_gate.update_domain_interval(target_url, cached_policy.crawl_delay)

        res = self._evaluate_cached_policy(cached_policy, target_url.normalized_url, token)
        if recorder is not None:
            total_elapsed = max(0.0, self._clock() - eval_start_t)
            recorder.robots_evaluation_duration_seconds += max(
                0.0, total_elapsed - robots_fetch_elapsed
            )
        return res

    def _build_policy(
        self,
        fetch_result: FetchResult,
        token: str,
        now: float,
    ) -> _CachedRobotsPolicy:
        if fetch_result.outcome == FetchOutcomeCode.SUCCESS:
            parser = urllib.robotparser.RobotFileParser()
            lines = (fetch_result.body_text or "").splitlines()
            parser.parse(lines)
            delay = _extract_crawl_delay(parser, fetch_result.body_text, token)

            return _CachedRobotsPolicy(
                policy_type="PARSED",
                parser=parser,
                crawl_delay=delay,
                reason="Parsed robots.txt rules successfully",
                expires_at=now + self._cache_ttl,
            )

        if (
            fetch_result.outcome == FetchOutcomeCode.HTTP_ERROR
            and fetch_result.status_code is not None
        ):
            status = fetch_result.status_code
            if status in {401, 403}:
                return _CachedRobotsPolicy(
                    policy_type="ALWAYS_DISALLOW",
                    parser=None,
                    crawl_delay=None,
                    reason=f"robots.txt access denied with HTTP status {status}",
                    expires_at=now + self._cache_ttl,
                )
            if status == 429 or status >= 500:
                return _CachedRobotsPolicy(
                    policy_type="TEMPORARY_FAILURE",
                    parser=None,
                    crawl_delay=None,
                    reason=f"robots.txt temporary failure with HTTP status {status}",
                    expires_at=now + self._temp_fail_ttl,
                )
            # Other 4xx status codes (404, 410, etc.) mean robots.txt unavailable -> ALLOW
            return _CachedRobotsPolicy(
                policy_type="ALWAYS_ALLOW",
                parser=None,
                crawl_delay=None,
                reason=f"robots.txt file unavailable (HTTP status {status})",
                expires_at=now + self._cache_ttl,
            )

        # Transport, timeout, DNS, or unsafe host failures -> TEMPORARY_FAILURE
        err_msg = fetch_result.error_message or fetch_result.outcome.value
        return _CachedRobotsPolicy(
            policy_type="TEMPORARY_FAILURE",
            parser=None,
            crawl_delay=None,
            reason=f"robots.txt fetch error: {err_msg}",
            expires_at=now + self._temp_fail_ttl,
        )

    def _evaluate_cached_policy(
        self,
        policy: _CachedRobotsPolicy,
        target_url_str: str,
        token: str,
    ) -> RobotsDecision:
        if policy.policy_type == "ALWAYS_ALLOW":
            return RobotsDecision(
                target_url=target_url_str,
                decision=RobotsDecisionCode.ALLOWED,
                crawl_delay=policy.crawl_delay,
                reason=policy.reason,
            )
        if policy.policy_type == "ALWAYS_DISALLOW":
            return RobotsDecision(
                target_url=target_url_str,
                decision=RobotsDecisionCode.DISALLOWED,
                crawl_delay=policy.crawl_delay,
                reason=policy.reason,
            )
        if policy.policy_type == "TEMPORARY_FAILURE":
            return RobotsDecision(
                target_url=target_url_str,
                decision=RobotsDecisionCode.TEMPORARY_FAILURE,
                crawl_delay=policy.crawl_delay,
                reason=policy.reason,
            )

        parser = policy.parser
        allowed = True
        if parser is not None:
            allowed = parser.can_fetch(token, target_url_str)

        decision_code = RobotsDecisionCode.ALLOWED if allowed else RobotsDecisionCode.DISALLOWED
        reason_text = (
            f"Allowed by robots.txt rules for '{token}'"
            if allowed
            else f"Disallowed by robots.txt rules for '{token}'"
        )
        return RobotsDecision(
            target_url=target_url_str,
            decision=decision_code,
            crawl_delay=policy.crawl_delay,
            reason=reason_text,
        )
