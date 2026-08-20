"""Phase 5B: Add fence_token, attempt_started_at, attempt_started_fence_token,
claimed_from_status, claimed_from_next_retry_at, last_claimed_at and candidate indexes.

Revision ID: 20260820_0001
Revises: 20260819_0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260820_0001"
down_revision: str | None = "20260819_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Add last_claimed_at to scan_jobs table
    op.add_column(
        "scan_jobs",
        sa.Column("last_claimed_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.create_index("ix_scan_jobs_last_claimed", "scan_jobs", ["last_claimed_at"])

    # 2. Add columns to scan_urls table
    op.add_column(
        "scan_urls",
        sa.Column("fence_token", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.add_column(
        "scan_urls",
        sa.Column("attempt_started_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.add_column(
        "scan_urls",
        sa.Column("attempt_started_fence_token", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "scan_urls",
        sa.Column("claimed_from_status", sa.String(length=50), nullable=True),
    )
    op.add_column(
        "scan_urls",
        sa.Column("claimed_from_next_retry_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )

    # 3. Create partial composite candidate index on scan_urls
    op.create_index(
        "ix_scan_urls_claim_candidate",
        "scan_urls",
        ["status", "next_retry_at", "attempt_count", "max_attempts", "scan_job_id"],
        postgresql_where=sa.text(
            "status IN ('QUEUED', 'RETRY_WAIT') AND attempt_count < max_attempts"
        ),
    )


def downgrade() -> None:
    op.drop_index("ix_scan_urls_claim_candidate", table_name="scan_urls")
    op.drop_column("scan_urls", "claimed_from_next_retry_at")
    op.drop_column("scan_urls", "claimed_from_status")
    op.drop_column("scan_urls", "attempt_started_fence_token")
    op.drop_column("scan_urls", "attempt_started_at")
    op.drop_column("scan_urls", "fence_token")

    op.drop_index("ix_scan_jobs_last_claimed", table_name="scan_jobs")
    op.drop_column("scan_jobs", "last_claimed_at")
