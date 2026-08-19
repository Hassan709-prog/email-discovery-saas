"""Phase 4A primary email selection schema changes.

- Add unique constraint uq_scan_urls_id_job on scan_urls(id, scan_job_id)
- Add scan_url_id column to email_findings
- Drop old constraint uq_email_findings_job_canonical
- Create partial unique index uq_email_findings_historical_job_canonical (WHERE scan_url_id IS NULL)
- Create partial unique index uq_email_findings_scan_url_not_null (WHERE scan_url_id IS NOT NULL)
- Create composite foreign key fk_email_findings_scan_url_job from
  (scan_url_id, scan_job_id) to scan_urls(id, scan_job_id)

Revision ID: 20260818_0001
Revises: 20260816_0005
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260818_0001"
down_revision: str | None = "20260816_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Add unique constraint on scan_urls (id, scan_job_id)
    op.create_unique_constraint(
        "uq_scan_urls_id_job",
        "scan_urls",
        ["id", "scan_job_id"],
    )

    # 2. Add scan_url_id column to email_findings
    op.add_column(
        "email_findings",
        sa.Column("scan_url_id", postgresql.UUID(as_uuid=True), nullable=True),
    )

    # 3. Drop legacy constraint uq_email_findings_job_canonical
    op.drop_constraint("uq_email_findings_job_canonical", "email_findings", type_="unique")

    # 4. Create partial index for historical rows (scan_url_id IS NULL)
    op.create_index(
        "uq_email_findings_historical_job_canonical",
        "email_findings",
        ["scan_job_id", "canonical_email"],
        unique=True,
        postgresql_where=sa.text("scan_url_id IS NULL"),
    )

    # 5. Create partial unique index enforcing at most 1 primary email per ScanURL
    op.create_index(
        "uq_email_findings_scan_url_not_null",
        "email_findings",
        ["scan_url_id"],
        unique=True,
        postgresql_where=sa.text("scan_url_id IS NOT NULL"),
    )

    # 6. Add composite foreign key linking (scan_url_id, scan_job_id) to scan_urls(id, scan_job_id)
    op.create_foreign_key(
        "fk_email_findings_scan_url_job",
        "email_findings",
        "scan_urls",
        ["scan_url_id", "scan_job_id"],
        ["id", "scan_job_id"],
        ondelete="CASCADE",
    )

    # 7. Add index on scan_url_id
    op.create_index(
        "ix_email_findings_scan_url_id",
        "email_findings",
        ["scan_url_id"],
    )


def downgrade() -> None:
    # Preflight check for duplicate (scan_job_id, canonical_email) across different scan_urls
    conn = op.get_bind()
    dup_res = conn.execute(
        sa.text(
            "SELECT scan_job_id, canonical_email, COUNT(*) FROM email_findings "
            "GROUP BY scan_job_id, canonical_email HAVING COUNT(*) > 1"
        )
    )
    duplicates = dup_res.fetchall()
    if duplicates:
        raise RuntimeError(
            f"Cannot downgrade: email_findings table contains {len(duplicates)} duplicate "
            "canonical email(s) within the same scan job across different scan_urls. "
            "Downgrading would cause data loss or constraint violation."
        )

    op.drop_index("ix_email_findings_scan_url_id", table_name="email_findings")
    op.drop_constraint("fk_email_findings_scan_url_job", "email_findings", type_="foreignkey")
    op.drop_index("uq_email_findings_scan_url_not_null", table_name="email_findings")
    op.drop_index("uq_email_findings_historical_job_canonical", table_name="email_findings")

    op.create_unique_constraint(
        "uq_email_findings_job_canonical",
        "email_findings",
        ["scan_job_id", "canonical_email"],
    )

    op.drop_column("email_findings", "scan_url_id")
    op.drop_constraint("uq_scan_urls_id_job", "scan_urls", type_="unique")
