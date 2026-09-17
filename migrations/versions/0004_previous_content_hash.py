"""Record the prior content hash so an upsert can classify its own outcome.

The repository previously classified INSERT / UNCHANGED / UPDATED by reading
the row in a CTE alongside the upsert. Both share one statement snapshot, so a
row committed by a concurrent writer after the statement began is invisible to
that read while the ON CONFLICT branch resolves against it. The classification
was therefore wrong under concurrency, and the company-conflict case returned
no row at all.

Capturing the previous hash inside the conflict branch removes the snapshot
dependency: `jobs.content_hash` there is the live row, read under the row lock
that ON CONFLICT already takes.

NULL means the row has never been re-observed, which is also how an INSERT is
told apart from an UPDATE without consulting system columns.

Revision ID: 0004_previous_content_hash
Revises: 0003_source_job_id_not_blank
Create Date: 2026-09-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_previous_content_hash"
down_revision: str | None = "0003_source_job_id_not_blank"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("previous_content_hash", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "previous_content_hash")
