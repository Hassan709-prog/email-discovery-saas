"""Explicit confirmation and sanitized operational recovery audit tests."""

import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from email_discovery_api.models import AuditLog
from email_discovery_api.services.operations import OperationalService, safe_digest
from email_discovery_api.services.scan_jobs import ScanJobService

pytestmark = pytest.mark.anyio


class RecordingSession:
    def __init__(self) -> None:
        self.records: list[object] = []

    @asynccontextmanager
    async def begin(self):  # type: ignore[no-untyped-def]
        yield

    def add(self, record: object) -> None:
        self.records.append(record)


async def test_recovery_uses_existing_boundary_and_records_sanitized_audit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job_id, org_id, user_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    session = RecordingSession()
    called = AsyncMock(return_value=SimpleNamespace(id=job_id))
    monkeypatch.setattr(ScanJobService, "reconcile_and_recover_stuck_job", called)
    service = OperationalService(
        session,  # type: ignore[arg-type]
        db_manager=SimpleNamespace(),
        redis_manager=SimpleNamespace(),
        settings=SimpleNamespace(operations_query_timeout_seconds=2),
    )
    response = await service.recover_job(
        organization_id=org_id,
        job_id=job_id,
        actor_user_id=user_id,
        request_id="safe-request-id",
    )
    called.assert_awaited_once_with(org_id, job_id)
    assert response.reference_digest == safe_digest(job_id)
    assert response.audit_recorded
    audit = session.records[0]
    assert isinstance(audit, AuditLog)
    assert audit.target_id == safe_digest(job_id)
    assert str(job_id) not in str(audit.metadata_)
    assert audit.metadata_ == {"outcome": "reconciled", "explicit_confirmation": True}
