"""Add redirect approval fields to scan_urls table.

Revision ID: 20260831_0001
Revises: 20260820_0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260831_0001"
down_revision: str | None = "20260820_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "scan_urls",
        sa.Column("approved_redirect_domain", sa.String(255), nullable=True),
    )
    op.add_column(
        "scan_urls",
        sa.Column("redirect_target_domain", sa.String(255), nullable=True),
    )
    op.add_column(
        "scan_urls",
        sa.Column("redirect_target_url", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("scan_urls", "redirect_target_url")
    op.drop_column("scan_urls", "redirect_target_domain")
    op.drop_column("scan_urls", "approved_redirect_domain")
