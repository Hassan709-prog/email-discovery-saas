"""Initial PostgreSQL schema for core identity, tenancy, authorization, scan jobs,
events, and audit logs.

Revision ID: 20260816_0001
Revises:
Create Date: 2026-08-16 16:25:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260816_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. organizations
    op.create_table(
        "organizations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("slug", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False, server_default="ACTIVE"),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint("status IN ('ACTIVE', 'SUSPENDED')", name="ck_organizations_status"),
        sa.UniqueConstraint("slug", name="uq_organizations_slug"),
    )
    op.create_index("ix_organizations_slug", "organizations", ["slug"], unique=True)

    # 2. users
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("normalized_email", sa.String(length=255), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=True),
        sa.Column("display_name", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=False, server_default="ACTIVE"),
        sa.Column("email_verified_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_login_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint("status IN ('ACTIVE', 'SUSPENDED', 'DELETED')", name="ck_users_status"),
        sa.UniqueConstraint("normalized_email", name="uq_users_normalized_email"),
    )
    op.create_index("ix_users_normalized_email", "users", ["normalized_email"], unique=True)

    # 3. memberships
    op.create_table(
        "memberships",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(length=50), nullable=False, server_default="MEMBER"),
        sa.Column("status", sa.String(length=50), nullable=False, server_default="ACTIVE"),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("organization_id", "user_id", name="uq_memberships_org_user"),
        sa.CheckConstraint(
            "role IN ('OWNER', 'ADMIN', 'MEMBER', 'VIEWER')", name="ck_memberships_role"
        ),
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'INVITED', 'SUSPENDED')", name="ck_memberships_status"
        ),
    )

    # 4. scan_jobs
    op.create_table(
        "scan_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=False, server_default="DRAFT"),
        sa.Column("source_type", sa.String(length=50), nullable=False, server_default="MANUAL"),
        sa.Column("scanner_version", sa.String(length=50), nullable=False, server_default="1.0.0"),
        sa.Column(
            "normalization_version", sa.String(length=50), nullable=False, server_default="1.0.0"
        ),
        sa.Column("ranking_version", sa.String(length=50), nullable=False, server_default="1.0.0"),
        sa.Column(
            "configuration_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("total_input_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("valid_input_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("duplicate_input_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("queued_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("running_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("email_finding_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("cancellation_requested_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.CheckConstraint(
            "status IN ('DRAFT', 'QUEUED', 'RUNNING', 'CANCELLING', 'CANCELLED', "
            "'COMPLETED', 'COMPLETED_WITH_ERRORS', 'FAILED')",
            name="ck_scan_jobs_status",
        ),
        sa.CheckConstraint(
            "source_type IN ('MANUAL', 'CSV', 'XLSX', 'API')", name="ck_scan_jobs_source_type"
        ),
        sa.CheckConstraint("total_input_count >= 0", name="ck_scan_jobs_total_nonnegative"),
        sa.CheckConstraint("valid_input_count >= 0", name="ck_scan_jobs_valid_nonnegative"),
        sa.CheckConstraint("duplicate_input_count >= 0", name="ck_scan_jobs_duplicate_nonnegative"),
        sa.CheckConstraint("queued_count >= 0", name="ck_scan_jobs_queued_nonnegative"),
        sa.CheckConstraint("running_count >= 0", name="ck_scan_jobs_running_nonnegative"),
        sa.CheckConstraint("completed_count >= 0", name="ck_scan_jobs_completed_nonnegative"),
        sa.CheckConstraint("failed_count >= 0", name="ck_scan_jobs_failed_nonnegative"),
        sa.CheckConstraint(
            "email_finding_count >= 0", name="ck_scan_jobs_email_findings_nonnegative"
        ),
        sa.CheckConstraint(
            "valid_input_count <= total_input_count", name="ck_scan_jobs_valid_le_total"
        ),
        sa.CheckConstraint(
            "duplicate_input_count <= total_input_count",
            name="ck_scan_jobs_duplicate_le_total",
        ),
        sa.CheckConstraint(
            "valid_input_count + duplicate_input_count <= total_input_count",
            name="ck_scan_jobs_valid_dup_le_total",
        ),
        sa.CheckConstraint(
            "queued_count + running_count + completed_count + failed_count <= valid_input_count",
            name="ck_scan_jobs_processed_le_valid",
        ),
    )
    op.create_index("ix_scan_jobs_org_created", "scan_jobs", ["organization_id", "created_at"])
    op.create_index("ix_scan_jobs_org_status", "scan_jobs", ["organization_id", "status"])

    # 5. scan_urls
    op.create_table(
        "scan_urls",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("scan_job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("original_index", sa.Integer(), nullable=False),
        sa.Column("original_input", sa.Text(), nullable=False),
        sa.Column("normalized_url", sa.Text(), nullable=True),
        sa.Column("normalized_domain", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=False, server_default="PENDING"),
        sa.Column("duplicate_of_scan_url_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("last_error_code", sa.String(length=100), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.Column("next_retry_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("lease_owner", sa.String(length=255), nullable=True),
        sa.Column("lease_expires_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["scan_job_id"], ["scan_jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["duplicate_of_scan_url_id"], ["scan_urls.id"], ondelete="SET NULL"
        ),
        sa.UniqueConstraint("scan_job_id", "original_index", name="uq_scan_urls_job_index"),
        sa.CheckConstraint(
            "status IN ('INVALID', 'PENDING', 'QUEUED', 'LEASED', 'SCANNING', "
            "'RETRY_WAIT', 'COMPLETED', 'NO_EMAIL', 'FAILED', 'CANCELLED', 'DUPLICATE')",
            name="ck_scan_urls_status",
        ),
        sa.CheckConstraint("original_index >= 0", name="ck_scan_urls_index_nonnegative"),
        sa.CheckConstraint("attempt_count >= 0", name="ck_scan_urls_attempts_nonnegative"),
        sa.CheckConstraint("max_attempts >= 0", name="ck_scan_urls_max_attempts_nonnegative"),
        sa.CheckConstraint("attempt_count <= max_attempts", name="ck_scan_urls_attempts_le_max"),
        sa.CheckConstraint(
            "duplicate_of_scan_url_id IS NULL OR duplicate_of_scan_url_id != id",
            name="ck_scan_urls_self_duplicate_prevented",
        ),
    )
    op.create_index("ix_scan_urls_job_status", "scan_urls", ["scan_job_id", "status"])
    op.create_index("ix_scan_urls_status_next_retry", "scan_urls", ["status", "next_retry_at"])
    op.create_index(
        "ix_scan_urls_status_lease_expires", "scan_urls", ["status", "lease_expires_at"]
    )

    # 6. job_events
    op.create_table(
        "job_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("scan_job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scan_url_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["scan_job_id"], ["scan_jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["scan_url_id"], ["scan_urls.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("scan_job_id", "sequence_number", name="uq_job_events_job_seq"),
        sa.CheckConstraint("sequence_number >= 1", name="ck_job_events_seq_positive"),
    )
    op.create_index("ix_job_events_job_created", "job_events", ["scan_job_id", "created_at"])

    # 7. audit_logs
    op.create_table(
        "audit_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("target_type", sa.String(length=100), nullable=False),
        sa.Column("target_id", sa.String(length=255), nullable=True),
        sa.Column("request_id", sa.String(length=128), nullable=True),
        sa.Column("before_values", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("after_values", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_audit_logs_org_created", "audit_logs", ["organization_id", "created_at"])
    op.create_index("ix_audit_logs_actor_created", "audit_logs", ["actor_user_id", "created_at"])
    op.create_index("ix_audit_logs_target", "audit_logs", ["target_type", "target_id"])


def downgrade() -> None:
    # Drop tables in reverse dependency order
    op.drop_index("ix_audit_logs_target", table_name="audit_logs")
    op.drop_index("ix_audit_logs_actor_created", table_name="audit_logs")
    op.drop_index("ix_audit_logs_org_created", table_name="audit_logs")
    op.drop_table("audit_logs")

    op.drop_index("ix_job_events_job_created", table_name="job_events")
    op.drop_table("job_events")

    op.drop_index("ix_scan_urls_status_lease_expires", table_name="scan_urls")
    op.drop_index("ix_scan_urls_status_next_retry", table_name="scan_urls")
    op.drop_index("ix_scan_urls_job_status", table_name="scan_urls")
    op.drop_table("scan_urls")

    op.drop_index("ix_scan_jobs_org_status", table_name="scan_jobs")
    op.drop_index("ix_scan_jobs_org_created", table_name="scan_jobs")
    op.drop_table("scan_jobs")

    op.drop_table("memberships")

    op.drop_index("ix_users_normalized_email", table_name="users")
    op.drop_table("users")

    op.drop_index("ix_organizations_slug", table_name="organizations")
    op.drop_table("organizations")
