"""Unit tests for SQLAlchemy 2 domain models, constraints, parity, and normalization helpers."""

import pytest
from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from sqlalchemy.orm import configure_mappers

from email_discovery_api.models import (
    AuditLog,
    Base,
    JobEvent,
    MembershipRole,
    MembershipStatus,
    OrganizationStatus,
    ScanJobSourceType,
    ScanJobStatus,
    ScanURLStatus,
    UserStatus,
    normalize_email,
    normalize_org_slug,
)


def test_mapper_configuration() -> None:
    """Verify all model relationships compile without ORM mapper configuration errors."""
    configure_mappers()


def test_metadata_tables_exist() -> None:
    """Verify Base.metadata contains all 7 required core database tables."""
    table_names = set(Base.metadata.tables.keys())
    expected_tables = {
        "organizations",
        "users",
        "memberships",
        "scan_jobs",
        "scan_urls",
        "job_events",
        "audit_logs",
    }
    assert expected_tables.issubset(table_names)


def test_audit_log_metadata_column_mapping() -> None:
    """Verify AuditLog metadata_ attribute maps to DB column 'metadata'."""
    table = Base.metadata.tables["audit_logs"]
    assert "metadata" in table.columns
    assert hasattr(AuditLog, "metadata_")


def test_foreign_key_ondelete_rules() -> None:
    """Verify explicit on-delete rules prevent accidental historical data loss."""
    scan_jobs = Base.metadata.tables["scan_jobs"]
    org_fk = next(fk for fk in scan_jobs.foreign_keys if fk.column.table.name == "organizations")
    assert org_fk.ondelete == "RESTRICT"

    user_fk = next(fk for fk in scan_jobs.foreign_keys if fk.column.table.name == "users")
    assert user_fk.ondelete == "SET NULL"

    job_events = Base.metadata.tables["job_events"]
    url_fk = next(fk for fk in job_events.foreign_keys if fk.column.table.name == "scan_urls")
    assert url_fk.ondelete == "SET NULL"

    audit_logs = Base.metadata.tables["audit_logs"]
    audit_org_fk = next(
        fk for fk in audit_logs.foreign_keys if fk.column.table.name == "organizations"
    )
    assert audit_org_fk.ondelete == "SET NULL"


def test_scan_url_no_global_uniqueness() -> None:
    """Verify ScanURL normalized_url and normalized_domain do not enforce global uniqueness."""
    table = Base.metadata.tables["scan_urls"]
    assert table.columns["normalized_url"].unique is not True
    assert table.columns["normalized_domain"].unique is not True


def test_scan_job_counter_constraints_exist() -> None:
    """Verify ScanJob check constraints enforce nonnegativity and mathematical boundaries."""
    table = Base.metadata.tables["scan_jobs"]
    ck_names = {ck.name for ck in table.constraints if ck.name}
    expected_cks = {
        "ck_scan_jobs_status",
        "ck_scan_jobs_source_type",
        "ck_scan_jobs_total_nonnegative",
        "ck_scan_jobs_valid_nonnegative",
        "ck_scan_jobs_duplicate_nonnegative",
        "ck_scan_jobs_queued_nonnegative",
        "ck_scan_jobs_running_nonnegative",
        "ck_scan_jobs_completed_nonnegative",
        "ck_scan_jobs_failed_nonnegative",
        "ck_scan_jobs_email_findings_nonnegative",
        "ck_scan_jobs_valid_le_total",
        "ck_scan_jobs_duplicate_le_total",
        "ck_scan_jobs_valid_dup_le_total",
        "ck_scan_jobs_processed_le_valid",
        "ck_scan_jobs_next_event_seq_positive",
        "ck_scan_jobs_fingerprint_hex",
        "ck_scan_jobs_idempotency_fingerprint_pair",
    }
    assert expected_cks.issubset(ck_names)


def test_scan_url_constraints_exist() -> None:
    """Verify ScanURL check constraints enforce index bounds and self-duplicate prevention."""
    table = Base.metadata.tables["scan_urls"]
    ck_names = {ck.name for ck in table.constraints if ck.name}
    expected_cks = {
        "ck_scan_urls_status",
        "ck_scan_urls_index_nonnegative",
        "ck_scan_urls_attempts_nonnegative",
        "ck_scan_urls_max_attempts_nonnegative",
        "ck_scan_urls_attempts_le_max",
        "ck_scan_urls_self_duplicate_prevented",
    }
    assert expected_cks.issubset(ck_names)


def test_append_only_models_no_mutation_methods() -> None:
    """Verify append-only models JobEvent and AuditLog expose no update methods."""
    for model_cls in (JobEvent, AuditLog):
        methods = [
            m for m in dir(model_cls) if callable(getattr(model_cls, m)) and not m.startswith("_")
        ]
        assert "update" not in methods
        assert "mutate" not in methods


def test_normalize_email_helper() -> None:
    """Test deterministic email normalization."""
    assert normalize_email(" User@Example.COM ") == "user@example.com"
    assert normalize_email("john.doe@company.org") == "john.doe@company.org"

    with pytest.raises(ValueError, match="Invalid email"):
        normalize_email("invalid-email")

    with pytest.raises(ValueError, match="Invalid email"):
        normalize_email("   ")


def test_normalize_org_slug_helper() -> None:
    """Test deterministic organization slug normalization."""
    assert normalize_org_slug(" Acme Corp! ") == "acme-corp"
    assert normalize_org_slug("   Software & Technology Inc.   ") == "software-technology-inc"
    assert normalize_org_slug("hello---world") == "hello-world"

    with pytest.raises(ValueError, match="cannot be empty"):
        normalize_org_slug("!!!")


def test_alembic_single_head_revision() -> None:
    """Verify Alembic migration revision history contains exactly one head."""
    alembic_cfg = AlembicConfig("apps/api/alembic.ini")
    script = ScriptDirectory.from_config(alembic_cfg)
    heads = script.get_heads()
    assert len(heads) == 1
    assert heads[0] == "20260816_0002"


def test_enum_values_synchronized() -> None:
    """Verify Enum string values match expected domain status constants."""
    assert OrganizationStatus.ACTIVE.value == "ACTIVE"
    assert UserStatus.DELETED.value == "DELETED"
    assert MembershipRole.OWNER.value == "OWNER"
    assert MembershipStatus.INVITED.value == "INVITED"
    assert ScanJobStatus.RUNNING.value == "RUNNING"
    assert ScanJobSourceType.API.value == "API"
    assert ScanURLStatus.RETRY_WAIT.value == "RETRY_WAIT"
