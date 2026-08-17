"""Add crawl attempts, crawled pages, email findings, evidence, and rejected candidates tables.

Revision ID: 20260816_0004
Revises: 20260816_0003
Create Date: 2026-08-16 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260816_0004"
down_revision: str | None = "20260816_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. CrawlAttempt table
    op.create_table(
        "crawl_attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scan_url_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("outcome", sa.String(length=50), nullable=False),
        sa.Column("retryable", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("requested_url", sa.Text(), nullable=False),
        sa.Column("final_url", sa.Text(), nullable=True),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(length=50), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("redirect_history", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("connection_attempts", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("elapsed_seconds", sa.Float(), nullable=True),
        sa.Column("result_checksum", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["scan_url_id"], ["scan_urls.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "scan_url_id", "attempt_number", name="uq_crawl_attempts_scan_url_attempt"
        ),
        sa.CheckConstraint("attempt_number >= 1", name="ck_crawl_attempts_attempt_number"),
        sa.CheckConstraint(
            "elapsed_seconds IS NULL OR elapsed_seconds >= 0.0",
            name="ck_crawl_attempts_elapsed_seconds",
        ),
        sa.CheckConstraint(
            "status_code IS NULL OR (status_code >= 100 AND status_code <= 599)",
            name="ck_crawl_attempts_status_code",
        ),
        sa.CheckConstraint(
            "result_checksum ~ '^[0-9a-f]{64}$'",
            name="ck_crawl_attempts_result_checksum_hex",
        ),
    )
    op.create_index(
        "ix_crawl_attempts_scan_url_created", "crawl_attempts", ["scan_url_id", "created_at"]
    )
    op.create_index(
        "ix_crawl_attempts_outcome_created", "crawl_attempts", ["outcome", "created_at"]
    )

    # 2. CrawledPage table
    op.create_table(
        "crawled_pages",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("crawl_attempt_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scan_url_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("normalized_url", sa.Text(), nullable=False),
        sa.Column("final_url", sa.Text(), nullable=False),
        sa.Column("depth", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("outcome", sa.String(length=50), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("content_type", sa.String(length=100), nullable=True),
        sa.Column("content_sha256", sa.String(length=64), nullable=True),
        sa.Column("page_score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ranking_version", sa.String(length=50), nullable=False),
        sa.Column("robots_decision", sa.String(length=50), nullable=True),
        sa.Column("links_discovered_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("emails_found_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("fetched_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["crawl_attempt_id"], ["crawl_attempts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["scan_url_id"], ["scan_urls.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "crawl_attempt_id", "normalized_url", name="uq_crawled_pages_attempt_url"
        ),
        sa.CheckConstraint("depth >= 0", name="ck_crawled_pages_depth"),
        sa.CheckConstraint(
            "page_score >= -100 AND page_score <= 1000", name="ck_crawled_pages_page_score"
        ),
        sa.CheckConstraint(
            "status_code IS NULL OR (status_code >= 100 AND status_code <= 599)",
            name="ck_crawled_pages_status_code",
        ),
        sa.CheckConstraint(
            "links_discovered_count >= 0 AND emails_found_count >= 0",
            name="ck_crawled_pages_counts",
        ),
        sa.CheckConstraint(
            "content_sha256 IS NULL OR content_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_crawled_pages_content_sha256_hex",
        ),
    )
    op.create_index("ix_crawled_pages_scan_url", "crawled_pages", ["scan_url_id"])
    op.create_index(
        "ix_crawled_pages_scan_url_normalized", "crawled_pages", ["scan_url_id", "normalized_url"]
    )
    op.create_index("ix_crawled_pages_final_url", "crawled_pages", ["final_url"])

    # 3. EmailFinding table
    op.create_table(
        "email_findings",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scan_job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("canonical_email", sa.String(length=255), nullable=False),
        sa.Column("email_domain", sa.String(length=255), nullable=False),
        sa.Column("classification", sa.String(length=50), nullable=False),
        sa.Column("is_role_based", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "validation_status", sa.String(length=50), nullable=False, server_default="'UNVERIFIED'"
        ),
        sa.Column("first_found_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("last_found_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("evidence_count", sa.Integer(), nullable=False, server_default="0"),
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "scan_job_id", "canonical_email", name="uq_email_findings_job_canonical"
        ),
        sa.CheckConstraint(
            "canonical_email = LOWER(canonical_email)",
            name="ck_email_findings_canonical_email_lower",
        ),
        sa.CheckConstraint(
            "email_domain = LOWER(email_domain)", name="ck_email_findings_email_domain_lower"
        ),
        sa.CheckConstraint("evidence_count >= 0", name="ck_email_findings_evidence_count"),
        sa.CheckConstraint("first_found_at <= last_found_at", name="ck_email_findings_timestamps"),
    )
    op.create_index(
        "ix_email_findings_job_canonical", "email_findings", ["scan_job_id", "canonical_email"]
    )
    op.create_index(
        "ix_email_findings_job_classification", "email_findings", ["scan_job_id", "classification"]
    )

    # 4. EmailEvidence table
    op.create_table(
        "email_evidence",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("email_finding_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("crawled_page_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_type", sa.String(length=50), nullable=False),
        sa.Column("raw_candidate", sa.String(length=255), nullable=True),
        sa.Column("snippet", sa.String(length=255), nullable=True),
        sa.Column("page_url", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("candidate_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["crawled_page_id"], ["crawled_pages.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["email_finding_id"], ["email_findings.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "email_finding_id",
            "crawled_page_id",
            "source_type",
            "candidate_hash",
            name="uq_email_evidence_finding_page_source_hash",
        ),
        sa.CheckConstraint(
            "confidence >= 0.0 AND confidence <= 1.0", name="ck_email_evidence_confidence"
        ),
        sa.CheckConstraint(
            "candidate_hash ~ '^[0-9a-f]{64}$'", name="ck_email_evidence_candidate_hash_hex"
        ),
    )

    # 5. RejectedEmailCandidate table
    op.create_table(
        "rejected_email_candidates",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scan_job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scan_url_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("crawled_page_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("candidate_hash", sa.String(length=64), nullable=False),
        sa.Column("masked_candidate", sa.String(length=255), nullable=True),
        sa.Column("rejection_code", sa.String(length=50), nullable=False),
        sa.Column("source_type", sa.String(length=50), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["crawled_page_id"], ["crawled_pages.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["scan_job_id"], ["scan_jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["scan_url_id"], ["scan_urls.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "scan_job_id",
            "candidate_hash",
            "rejection_code",
            name="uq_rejected_candidates_job_hash_code",
        ),
        sa.CheckConstraint(
            "candidate_hash ~ '^[0-9a-f]{64}$'", name="ck_rejected_candidates_hash_hex"
        ),
    )
    op.create_index(
        "ix_rejected_candidates_job_created",
        "rejected_email_candidates",
        ["scan_job_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_rejected_candidates_job_created", table_name="rejected_email_candidates")
    op.drop_table("rejected_email_candidates")
    op.drop_table("email_evidence")
    op.drop_index("ix_email_findings_job_classification", table_name="email_findings")
    op.drop_index("ix_email_findings_job_canonical", table_name="email_findings")
    op.drop_table("email_findings")
    op.drop_index("ix_crawled_pages_final_url", table_name="crawled_pages")
    op.drop_index("ix_crawled_pages_scan_url_normalized", table_name="crawled_pages")
    op.drop_index("ix_crawled_pages_scan_url", table_name="crawled_pages")
    op.drop_table("crawled_pages")
    op.drop_index("ix_crawl_attempts_outcome_created", table_name="crawl_attempts")
    op.drop_index("ix_crawl_attempts_scan_url_created", table_name="crawl_attempts")
    op.drop_table("crawl_attempts")
