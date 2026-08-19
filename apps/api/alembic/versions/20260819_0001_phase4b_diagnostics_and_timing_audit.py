"""Phase 4B diagnostics and timing audit schema changes.

- Add summary diagnostic columns and check constraints to scan_urls
- Add boundary attempt timing columns and failure_code to crawl_attempts

Revision ID: 20260819_0001
Revises: 20260818_0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260819_0001"
down_revision: str | None = "20260818_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Add columns and check constraints to scan_urls
    op.add_column("scan_urls", sa.Column("total_duration_seconds", sa.Float(), nullable=True))
    op.add_column("scan_urls", sa.Column("pages_attempted", sa.Integer(), nullable=True))
    op.add_column("scan_urls", sa.Column("pages_fetched", sa.Integer(), nullable=True))
    op.add_column("scan_urls", sa.Column("retry_count", sa.Integer(), nullable=True))
    op.add_column("scan_urls", sa.Column("last_failure_code", sa.String(50), nullable=True))

    op.create_check_constraint(
        "ck_scan_urls_total_duration_nonnegative",
        "scan_urls",
        "total_duration_seconds IS NULL OR total_duration_seconds >= 0.0",
    )
    op.create_check_constraint(
        "ck_scan_urls_pages_attempted_nonnegative",
        "scan_urls",
        "pages_attempted IS NULL OR pages_attempted >= 0",
    )
    op.create_check_constraint(
        "ck_scan_urls_pages_fetched_nonnegative",
        "scan_urls",
        "pages_fetched IS NULL OR pages_fetched >= 0",
    )
    op.create_check_constraint(
        "ck_scan_urls_retry_count_nonnegative",
        "scan_urls",
        "retry_count IS NULL OR retry_count >= 0",
    )

    # 2. Add columns and check constraints to crawl_attempts
    op.add_column("crawl_attempts", sa.Column("dns_duration_seconds", sa.Float(), nullable=True))
    op.add_column("crawl_attempts", sa.Column("gate_wait_seconds", sa.Float(), nullable=True))
    op.add_column("crawl_attempts", sa.Column("robots_duration_seconds", sa.Float(), nullable=True))
    op.add_column("crawl_attempts", sa.Column("http_duration_seconds", sa.Float(), nullable=True))
    op.add_column("crawl_attempts", sa.Column("parse_duration_seconds", sa.Float(), nullable=True))
    op.add_column("crawl_attempts", sa.Column("failure_code", sa.String(50), nullable=True))

    op.create_check_constraint(
        "ck_crawl_attempts_dns_duration_nonnegative",
        "crawl_attempts",
        "dns_duration_seconds IS NULL OR dns_duration_seconds >= 0.0",
    )
    op.create_check_constraint(
        "ck_crawl_attempts_gate_wait_nonnegative",
        "crawl_attempts",
        "gate_wait_seconds IS NULL OR gate_wait_seconds >= 0.0",
    )
    op.create_check_constraint(
        "ck_crawl_attempts_robots_duration_nonnegative",
        "crawl_attempts",
        "robots_duration_seconds IS NULL OR robots_duration_seconds >= 0.0",
    )
    op.create_check_constraint(
        "ck_crawl_attempts_http_duration_nonnegative",
        "crawl_attempts",
        "http_duration_seconds IS NULL OR http_duration_seconds >= 0.0",
    )
    op.create_check_constraint(
        "ck_crawl_attempts_parse_duration_nonnegative",
        "crawl_attempts",
        "parse_duration_seconds IS NULL OR parse_duration_seconds >= 0.0",
    )


def downgrade() -> None:
    # 1. Drop check constraints and columns from crawl_attempts
    op.drop_constraint(
        "ck_crawl_attempts_parse_duration_nonnegative", "crawl_attempts", type_="check"
    )
    op.drop_constraint(
        "ck_crawl_attempts_http_duration_nonnegative", "crawl_attempts", type_="check"
    )
    op.drop_constraint(
        "ck_crawl_attempts_robots_duration_nonnegative", "crawl_attempts", type_="check"
    )
    op.drop_constraint("ck_crawl_attempts_gate_wait_nonnegative", "crawl_attempts", type_="check")
    op.drop_constraint(
        "ck_crawl_attempts_dns_duration_nonnegative", "crawl_attempts", type_="check"
    )

    op.drop_column("crawl_attempts", "failure_code")
    op.drop_column("crawl_attempts", "parse_duration_seconds")
    op.drop_column("crawl_attempts", "http_duration_seconds")
    op.drop_column("crawl_attempts", "robots_duration_seconds")
    op.drop_column("crawl_attempts", "gate_wait_seconds")
    op.drop_column("crawl_attempts", "dns_duration_seconds")

    # 2. Drop check constraints and columns from scan_urls
    op.drop_constraint("ck_scan_urls_retry_count_nonnegative", "scan_urls", type_="check")
    op.drop_constraint("ck_scan_urls_pages_fetched_nonnegative", "scan_urls", type_="check")
    op.drop_constraint("ck_scan_urls_pages_attempted_nonnegative", "scan_urls", type_="check")
    op.drop_constraint("ck_scan_urls_total_duration_nonnegative", "scan_urls", type_="check")

    op.drop_column("scan_urls", "last_failure_code")
    op.drop_column("scan_urls", "retry_count")
    op.drop_column("scan_urls", "pages_fetched")
    op.drop_column("scan_urls", "pages_attempted")
    op.drop_column("scan_urls", "total_duration_seconds")
