"""Record job deactivation and crawl completeness (ADR-0017).

`jobs.deactivated_at` is when a job was found absent from a verified,
successful crawl of its company; NULL while the job is active, and cleared
when it is observed again. `jobs_deactivated` counts the stored jobs a crawl
deactivated; it counts stored jobs, not fetched postings, so it stays outside
the `fetched = new + existing + invalid` identity. `completeness_verified`
records whether the provider confirmed the fetch was the whole board; NULL
for a failed company, which fetched nothing usable.

Revision ID: 0005_job_deactivation
Revises: 0004_previous_content_hash
Create Date: 2026-11-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_job_deactivation"
down_revision: str | None = "0004_previous_content_hash"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COUNTED = ("crawl_runs", "crawl_company_results")


def upgrade() -> None:
    op.add_column(
        "jobs", sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True)
    )
    for table in _COUNTED:
        op.add_column(
            table,
            sa.Column(
                "jobs_deactivated", sa.Integer(), server_default="0", nullable=False
            ),
        )
        op.create_check_constraint(
            op.f(f"ck_{table}_deactivated_non_negative"),
            table,
            "jobs_deactivated >= 0",
        )
    op.add_column(
        "crawl_company_results",
        sa.Column("completeness_verified", sa.Boolean(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("crawl_company_results", "completeness_verified")
    for table in reversed(_COUNTED):
        op.drop_constraint(
            op.f(f"ck_{table}_deactivated_non_negative"), table, type_="check"
        )
        op.drop_column(table, "jobs_deactivated")
    op.drop_column("jobs", "deactivated_at")
