"""Add composite index for chronological evidence keyset pagination.

Revision ID: 20260816_0005
Revises: 20260816_0004
Create Date: 2026-08-17 14:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260816_0005"
down_revision: str | None = "20260816_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_email_evidence_finding_created_id",
        "email_evidence",
        ["email_finding_id", "created_at", "id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_email_evidence_finding_created_id", table_name="email_evidence")
