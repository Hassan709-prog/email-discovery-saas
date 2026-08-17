"""Add user auth_version and refresh_sessions table.

Revision ID: 20260816_0003
Revises: 20260816_0002
Create Date: 2026-08-16 17:35:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260816_0003"
down_revision: str | None = "20260816_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Add auth_version column and constraint to users
    op.add_column(
        "users",
        sa.Column("auth_version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_check_constraint(
        "ck_users_auth_version",
        "users",
        "auth_version >= 1",
    )

    # 2. Create refresh_sessions table
    op.create_table(
        "refresh_sessions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("family_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("csrf_token_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False, server_default="ACTIVE"),
        sa.Column("parent_session_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("replaced_by_session_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("auth_version", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("used_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_refresh_sessions_user_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_refresh_sessions_organization_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["parent_session_id"],
            ["refresh_sessions.id"],
            name="fk_refresh_sessions_parent_session_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["replaced_by_session_id"],
            ["refresh_sessions.id"],
            name="fk_refresh_sessions_replaced_by_session_id",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_refresh_sessions"),
        sa.UniqueConstraint("token_hash", name="uq_refresh_sessions_token_hash"),
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'ROTATED', 'REVOKED', 'COMPROMISED')",
            name="ck_refresh_sessions_status",
        ),
        sa.CheckConstraint(
            "token_hash ~ '^[0-9a-f]{64}$'",
            name="ck_refresh_sessions_token_hash_hex",
        ),
        sa.CheckConstraint(
            "csrf_token_hash ~ '^[0-9a-f]{64}$'",
            name="ck_refresh_sessions_csrf_token_hash_hex",
        ),
        sa.CheckConstraint(
            "auth_version >= 1",
            name="ck_refresh_sessions_auth_version",
        ),
        sa.CheckConstraint(
            "expires_at > created_at",
            name="ck_refresh_sessions_expires_at",
        ),
        sa.CheckConstraint(
            "parent_session_id IS NULL OR parent_session_id != id",
            name="ck_refresh_sessions_parent_not_self",
        ),
        sa.CheckConstraint(
            "replaced_by_session_id IS NULL OR replaced_by_session_id != id",
            name="ck_refresh_sessions_replaced_by_not_self",
        ),
    )

    # 3. Create indexes
    op.create_index(
        "ix_refresh_sessions_user_status",
        "refresh_sessions",
        ["user_id", "status"],
    )
    op.create_index(
        "ix_refresh_sessions_family_status",
        "refresh_sessions",
        ["family_id", "status"],
    )
    op.create_index(
        "ix_refresh_sessions_expires_at",
        "refresh_sessions",
        ["expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_refresh_sessions_expires_at", table_name="refresh_sessions")
    op.drop_index("ix_refresh_sessions_family_status", table_name="refresh_sessions")
    op.drop_index("ix_refresh_sessions_user_status", table_name="refresh_sessions")
    op.drop_table("refresh_sessions")
    op.drop_constraint("ck_users_auth_version", "users", type_="check")
    op.drop_column("users", "auth_version")
