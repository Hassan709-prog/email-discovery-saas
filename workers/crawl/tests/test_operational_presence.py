"""Advisory-only worker presence registry tests."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from email_discovery_crawl_worker.config import WorkerSettings
from email_discovery_crawl_worker.presence import WorkerPresenceManager

pytestmark = pytest.mark.anyio


async def test_presence_uses_bounded_advisory_registry_without_worker_name() -> None:
    client = MagicMock()
    client.time = AsyncMock(return_value=(1_700_000_000, 0))
    pipeline = MagicMock()
    pipeline.set.return_value = pipeline
    pipeline.zadd.return_value = pipeline
    pipeline.zremrangebyscore.return_value = pipeline
    pipeline.execute = AsyncMock(return_value=[])
    client.pipeline.return_value = pipeline
    settings = WorkerSettings(
        instance_id="fixed-instance",
        worker_label="private-worker-name",
        concurrency=3,
    )
    manager = WorkerPresenceManager(client, settings)

    assert await manager.update_presence("ACTIVE", 2)
    payload = json.loads(pipeline.set.call_args.args[1])
    assert payload["active_claims"] == 2
    assert payload["concurrency"] == 3
    assert "worker_label" not in payload
    pipeline.zadd.assert_called_once()
    pipeline.zremrangebyscore.assert_called_once()


async def test_graceful_removal_clears_key_and_registry_member() -> None:
    client = MagicMock()
    pipeline = MagicMock()
    pipeline.delete.return_value = pipeline
    pipeline.zrem.return_value = pipeline
    pipeline.execute = AsyncMock(return_value=[])
    client.pipeline.return_value = pipeline
    manager = WorkerPresenceManager(client, WorkerSettings(instance_id="fixed-instance"))
    await manager.remove_presence()
    pipeline.delete.assert_called_once_with(manager.presence_key)
    pipeline.zrem.assert_called_once_with(manager.registry_key, manager.instance_digest)
