"""Initial schema: companies, jobs, crawl_runs, crawl_company_results.

Creates the four tables of the MVP persistence model. Statuses are text with
CHECK constraints rather than native enums (ADR-0008), because adding a value
to a PostgreSQL enum is an awkward migration and more statuses are likely.

Revision ID: 73e5941d42ce
Revises: none (initial revision)
Create Date: 2026-09-16 02:52:42.765797+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "73e5941d42ce"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "companies",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("board_token", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), server_default="true", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_companies")),
        sa.UniqueConstraint("source", "board_token", name="uq_companies_source_token"),
    )
    op.create_table(
        "crawl_runs",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("companies_total", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "companies_succeeded", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column("companies_failed", sa.Integer(), server_default="0", nullable=False),
        sa.Column("jobs_fetched", sa.Integer(), server_default="0", nullable=False),
        sa.Column("jobs_new", sa.Integer(), server_default="0", nullable=False),
        sa.Column("jobs_existing", sa.Integer(), server_default="0", nullable=False),
        sa.Column("jobs_updated", sa.Integer(), server_default="0", nullable=False),
        sa.Column("jobs_invalid", sa.Integer(), server_default="0", nullable=False),
        sa.CheckConstraint(
            "status IN ('running', 'success', 'partial_success', 'failed')",
            name=op.f("ck_crawl_runs_status"),
        ),
        sa.CheckConstraint(
            "companies_total >= 0 AND companies_succeeded >= 0 "
            "AND companies_failed >= 0 AND jobs_fetched >= 0 AND jobs_new >= 0 "
            "AND jobs_existing >= 0 AND jobs_updated >= 0 AND jobs_invalid >= 0",
            name=op.f("ck_crawl_runs_counters_non_negative"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_crawl_runs")),
    )
    op.create_table(
        "crawl_company_results",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("crawl_run_id", sa.BigInteger(), nullable=False),
        sa.Column("company_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("jobs_fetched", sa.Integer(), server_default="0", nullable=False),
        sa.Column("jobs_new", sa.Integer(), server_default="0", nullable=False),
        sa.Column("jobs_existing", sa.Integer(), server_default="0", nullable=False),
        sa.Column("jobs_updated", sa.Integer(), server_default="0", nullable=False),
        sa.Column("jobs_invalid", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error_type", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('success', 'failed')",
            name=op.f("ck_crawl_company_results_status"),
        ),
        sa.CheckConstraint(
            "jobs_fetched >= 0 AND jobs_new >= 0 AND jobs_existing >= 0 "
            "AND jobs_updated >= 0 AND jobs_invalid >= 0",
            name=op.f("ck_crawl_company_results_counters_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("fk_crawl_company_results_company_id_companies"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["crawl_run_id"],
            ["crawl_runs.id"],
            name=op.f("fk_crawl_company_results_crawl_run_id_crawl_runs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_crawl_company_results")),
        sa.UniqueConstraint(
            "crawl_run_id", "company_id", name="uq_crawl_company_results_run_company"
        ),
    )
    op.create_index(
        "ix_crawl_company_results_company_status_finished",
        "crawl_company_results",
        ["company_id", "status", "finished_at"],
        unique=False,
    )
    op.create_table(
        "jobs",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("source_job_id", sa.Text(), nullable=False),
        sa.Column("company_id", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("location", sa.Text(), nullable=True),
        sa.Column("department", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("job_url", sa.Text(), nullable=False),
        sa.Column("apply_url", sa.Text(), nullable=True),
        sa.Column("source_published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("raw_data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("fk_jobs_company_id_companies"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_jobs")),
        sa.UniqueConstraint("source", "source_job_id", name="uq_jobs_source_job_id"),
    )
    op.create_index("ix_jobs_company_id", "jobs", ["company_id"], unique=False)
    op.create_index("ix_jobs_first_seen_at", "jobs", ["first_seen_at"], unique=False)
    op.create_index("ix_jobs_last_seen_at", "jobs", ["last_seen_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_jobs_last_seen_at", table_name="jobs")
    op.drop_index("ix_jobs_first_seen_at", table_name="jobs")
    op.drop_index("ix_jobs_company_id", table_name="jobs")
    op.drop_table("jobs")
    op.drop_index(
        "ix_crawl_company_results_company_status_finished",
        table_name="crawl_company_results",
    )
    op.drop_table("crawl_company_results")
    op.drop_table("crawl_runs")
    op.drop_table("companies")
