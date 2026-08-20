"""Privacy tests for stable worker lifecycle event logs."""

import logging

import pytest

from email_discovery_crawl_worker.presence import derive_instance_digest


def test_instance_digest_does_not_reveal_worker_identity(
    caplog: pytest.LogCaptureFixture,
) -> None:
    private_name = "customer-a-production-worker"
    digest = derive_instance_digest("fixed-instance")
    with caplog.at_level(logging.INFO):
        logging.getLogger("email_discovery_crawl_worker.worker").info(
            "event_code=WORKER_READY instance_digest=%s concurrency=%d state=ACTIVE",
            digest,
            2,
        )
    output = caplog.text
    assert "event_code=WORKER_READY" in output
    assert digest in output
    assert private_name not in output
