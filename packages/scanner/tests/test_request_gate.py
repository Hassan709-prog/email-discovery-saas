"""Tests for DomainRequestGate atomic reservation and rate limiting."""

import asyncio

from email_scanner.normalization import normalize_url
from email_scanner.request_gate import DomainRequestGate, get_domain_key


class FakeClock:
    def __init__(self, start: float = 100.0) -> None:
        self.current_time = start

    def __call__(self) -> float:
        return self.current_time

    def advance(self, seconds: float) -> None:
        self.current_time += seconds


def test_domain_key_extraction_registrable_and_ip() -> None:
    norm_domain = normalize_url("https://sub.acme.com/page")
    assert get_domain_key(norm_domain) == "acme.com"

    norm_ip1 = normalize_url("http://192.168.1.1/page")
    norm_ip2 = normalize_url("http://192.168.1.2/page")

    # Distinct IP hostnames produce distinct domain keys
    assert get_domain_key(norm_ip1) == "192.168.1.1"
    assert get_domain_key(norm_ip2) == "192.168.1.2"
    assert get_domain_key(norm_ip1) != get_domain_key(norm_ip2)


def test_atomic_same_domain_slot_reservation_race() -> None:
    async def _test() -> None:
        clock = FakeClock(start=100.0)
        sleep_history: list[float] = []

        async def fake_sleeper(seconds: float) -> None:
            sleep_history.append(seconds)

        gate = DomainRequestGate(
            default_minimum_interval_seconds=2.0,
            clock=clock,
            async_sleeper=fake_sleeper,
        )

        target = normalize_url("https://acme.com/page")

        # Simultaneously acquire gate for 3 callers
        await asyncio.gather(
            gate.acquire(target),
            gate.acquire(target),
            gate.acquire(target),
        )

        domain_key = get_domain_key(target)
        scheduled_times = gate.get_scheduled_times(domain_key)

        # Verify 3 distinct atomic request slots: 100.0, 102.0, 104.0
        assert len(scheduled_times) == 3
        assert scheduled_times[0] == 100.0
        assert scheduled_times[1] == 102.0
        assert scheduled_times[2] == 104.0

    asyncio.run(_test())
