"""Enforce that a job's source matches its company's source.

`jobs` carries both `company_id` and `source`, and before this migration
nothing required them to agree: a Lever job could reference a Greenhouse
company. The fix is a composite foreign key, which needs `(id, source)` to be
a unique key on `companies` first.

RESTRICT is kept on delete and extended to update, so a company with jobs can
neither be deleted nor have its source changed out from under them.

`UNIQUE (source, source_job_id)` on `jobs` is untouched.

Revision ID: 0002_source_consistency
Revises: 73e5941d42ce
Create Date: 2026-09-16
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002_source_consistency"
down_revision: str | None = "73e5941d42ce"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SIMPLE_FK = "fk_jobs_company_id_companies"
COMPOSITE_FK = "fk_jobs_company_id_source_companies"
COMPANY_UNIQUE = "uq_companies_id_source"


def upgrade() -> None:
    op.create_unique_constraint(COMPANY_UNIQUE, "companies", ["id", "source"])
    op.drop_constraint(SIMPLE_FK, "jobs", type_="foreignkey")
    op.create_foreign_key(
        COMPOSITE_FK,
        "jobs",
        "companies",
        ["company_id", "source"],
        ["id", "source"],
        ondelete="RESTRICT",
        onupdate="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(COMPOSITE_FK, "jobs", type_="foreignkey")
    op.create_foreign_key(
        SIMPLE_FK,
        "jobs",
        "companies",
        ["company_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.drop_constraint(COMPANY_UNIQUE, "companies", type_="unique")
