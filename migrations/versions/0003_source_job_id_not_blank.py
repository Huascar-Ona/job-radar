"""Reject blank source_job_id at the database level.

`(source, source_job_id)` is the job identity, so an empty or whitespace-only
id is not a job that happens to be badly named: it is a row with no identity.

The domain model already rejects `""`, `"   "` and tab/newline-only values,
but the database is the last line of defence and not every future write path
is the ORM.

`source_job_id ~ '\\S'` requires at least one non-whitespace character. A
`btrim(source_job_id) <> ''` check would have been wrong: btrim's default
character list is spaces only, so tab- and newline-only ids would pass.

Revision ID: 0003_source_job_id_not_blank
Revises: 0002_source_consistency
Create Date: 2026-09-16
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003_source_job_id_not_blank"
down_revision: str | None = "0002_source_consistency"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Bare name: the metadata naming convention expands it to
# ck_jobs_source_job_id_not_blank on both create and drop.
CONSTRAINT = "source_job_id_not_blank"


def upgrade() -> None:
    op.create_check_constraint(CONSTRAINT, "jobs", r"source_job_id ~ '\S'")


def downgrade() -> None:
    op.drop_constraint(CONSTRAINT, "jobs", type_="check")
