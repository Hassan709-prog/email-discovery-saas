"""Add idempotency key, request fingerprint, and atomic event sequence to scan_jobs.

Revision ID: 20260816_0002
Revises: 20260816_0001
Create Date: 2026-08-16 16:38:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260816_0002"
down_revision: str | None = "20260816_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Add columns to scan_jobs
    op.add_column(
        "scan_jobs",
        sa.Column("idempotency_key", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "scan_jobs",
        sa.Column("request_fingerprint", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "scan_jobs",
        sa.Column(
            "next_event_sequence",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
    )

    # 2. Add partial unique index for tenant-scoped idempotency
    op.create_index(
        "uq_scan_jobs_org_idempotency",
        "scan_jobs",
        ["organization_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )

    # 3. Add check constraints for next_event_sequence and fingerprint validity
    op.create_check_constraint(
        "ck_scan_jobs_next_event_seq_positive",
        "scan_jobs",
        "next_event_sequence >= 1",
    )
    op.create_check_constraint(
        "ck_scan_jobs_fingerprint_hex",
        "scan_jobs",
        "request_fingerprint IS NULL OR "
        "(length(request_fingerprint) = 64 AND request_fingerprint ~ '^[0-9a-f]{64}$')",
    )
    op.create_check_constraint(
        "ck_scan_jobs_idempotency_fingerprint_pair",
        "scan_jobs",
        "idempotency_key IS NULL OR request_fingerprint IS NOT NULL",
    )


def downgrade() -> None:
    op.drop_constraint("ck_scan_jobs_idempotency_fingerprint_pair", "scan_jobs", type_="check")
    op.drop_constraint("ck_scan_jobs_fingerprint_hex", "scan_jobs", type_="check")
    op.drop_constraint("ck_scan_jobs_next_event_seq_positive", "scan_jobs", type_="check")
    op.drop_index("uq_scan_jobs_org_idempotency", table_name="scan_jobs")
    op.drop_column("scan_jobs", "next_event_sequence")
    op.drop_column("scan_jobs", "request_fingerprint")
    op.drop_column("scan_jobs", "idempotency_key")
