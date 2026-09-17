"""Persisting job observations.

Idempotency is a database property here, not an application convention. Every
write is a single `INSERT ... ON CONFLICT (source, source_job_id)` statement,
so two concurrent writers cannot both create the row: one inserts, the other
takes the conflict branch. A read-then-write would let both read "absent".
"""

import hashlib
import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from job_radar.domain.ingestion import JobUpsertOutcome, JobUpsertResult
from job_radar.domain.job import NormalizedJob

#: Fields whose change makes a stored job stale. Deliberately excludes
#: observation time: re-seeing an unchanged job is not a change.
HASHED_FIELDS = (
    "title",
    "location",
    "department",
    "description",
    "job_url",
    "apply_url",
    "source_published_at",
    "source_updated_at",
    "raw_data",
)

# One statement, one round trip. Every SET is guarded by the company matching,
# so a job whose identity already belongs to another company is left exactly as
# it is while still returning its row: the caller gets a reportable anomaly
# rather than an exception.
#
# Classification comes from the returned row, not from a separate read:
#   previous_content_hash IS NULL  -> this statement inserted
#   company_id != incoming         -> the identity belongs elsewhere
#   previous_content_hash == new   -> nothing about the source data changed
# The conflict branch evaluates `jobs.*` against the live row under the lock
# ON CONFLICT already holds, so the answer cannot be stale (ADR-0015).
_UPSERT = text("""
INSERT INTO jobs (
    source, source_job_id, company_id, title, location, department,
    description, job_url, apply_url, source_published_at, source_updated_at,
    first_seen_at, last_seen_at, is_active, raw_data, content_hash
) VALUES (
    :source, :source_job_id, :company_id, :title, :location, :department,
    :description, :job_url, :apply_url, :source_published_at,
    :source_updated_at, :observed_at, :observed_at, true,
    CAST(:raw_data AS jsonb), :content_hash
)
ON CONFLICT (source, source_job_id) DO UPDATE SET
    -- previous_content_hash is captured before content_hash is overwritten.
    -- first_seen_at is deliberately absent: an existing row keeps it.
    previous_content_hash = CASE
        WHEN jobs.company_id = EXCLUDED.company_id THEN jobs.content_hash
        ELSE jobs.previous_content_hash
    END,
    last_seen_at = CASE
        WHEN jobs.company_id = EXCLUDED.company_id THEN EXCLUDED.last_seen_at
        ELSE jobs.last_seen_at
    END,
    is_active = CASE
        WHEN jobs.company_id = EXCLUDED.company_id THEN true
        ELSE jobs.is_active
    END,
    updated_at = CASE
        WHEN jobs.company_id = EXCLUDED.company_id THEN now()
        ELSE jobs.updated_at
    END,
    title = CASE
        WHEN jobs.company_id = EXCLUDED.company_id THEN EXCLUDED.title
        ELSE jobs.title
    END,
    location = CASE
        WHEN jobs.company_id = EXCLUDED.company_id THEN EXCLUDED.location
        ELSE jobs.location
    END,
    department = CASE
        WHEN jobs.company_id = EXCLUDED.company_id THEN EXCLUDED.department
        ELSE jobs.department
    END,
    description = CASE
        WHEN jobs.company_id = EXCLUDED.company_id THEN EXCLUDED.description
        ELSE jobs.description
    END,
    job_url = CASE
        WHEN jobs.company_id = EXCLUDED.company_id THEN EXCLUDED.job_url
        ELSE jobs.job_url
    END,
    apply_url = CASE
        WHEN jobs.company_id = EXCLUDED.company_id THEN EXCLUDED.apply_url
        ELSE jobs.apply_url
    END,
    source_published_at = CASE
        WHEN jobs.company_id = EXCLUDED.company_id THEN EXCLUDED.source_published_at
        ELSE jobs.source_published_at
    END,
    source_updated_at = CASE
        WHEN jobs.company_id = EXCLUDED.company_id THEN EXCLUDED.source_updated_at
        ELSE jobs.source_updated_at
    END,
    content_hash = CASE
        WHEN jobs.company_id = EXCLUDED.company_id THEN EXCLUDED.content_hash
        ELSE jobs.content_hash
    END,
    -- The payload is the only large column and the hash covers exactly the
    -- fields it holds, so an unchanged job does not re-write ~10 KB of JSONB
    -- (ADR-0011).
    raw_data = CASE
        WHEN jobs.company_id <> EXCLUDED.company_id THEN jobs.raw_data
        WHEN jobs.content_hash IS DISTINCT FROM EXCLUDED.content_hash
            THEN EXCLUDED.raw_data
        ELSE jobs.raw_data
    END
RETURNING id, company_id, previous_content_hash
""")


def content_hash(job: NormalizedJob) -> str:
    """Stable hash over the source-derived fields of an observation.

    Covers exactly what a change in the source would alter. If it covered the
    raw payload instead, volatile fields unrelated to the posting would defeat
    the guard; if it covered observation time, every crawl would look like a
    change.
    """
    payload = {field: _hashable(getattr(job, field)) for field in sorted(HASHED_FIELDS)}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _hashable(value: object) -> object:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _hashable(item) for key, item in sorted(value.items())}
    if isinstance(value, list):
        return [_hashable(item) for item in value]
    return value


class JobRepository:
    """Writes job observations. Never commits; the caller owns the transaction."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def upsert(self, job: NormalizedJob, *, company_id: int) -> JobUpsertResult:
        """Persist one observation and report what happened.

        Raises:
            IntegrityError: any database integrity failure other than the
                expected identity conflict, which is reported as an outcome.
                A missing company or a source/company mismatch must surface,
                not be mistaken for "already existing".
        """
        incoming_hash = content_hash(job)
        parameters: dict[str, Any] = {
            "source": str(job.source),
            "source_job_id": job.source_job_id,
            "company_id": company_id,
            "title": job.title,
            "location": job.location,
            "department": job.department,
            "description": job.description,
            "job_url": job.job_url,
            "apply_url": job.apply_url,
            "source_published_at": job.source_published_at,
            "source_updated_at": job.source_updated_at,
            "observed_at": job.observed_at,
            "raw_data": json.dumps(job.raw_data, sort_keys=True),
            "content_hash": incoming_hash,
        }
        record = self._session.execute(_UPSERT, parameters).one()

        if record.company_id != company_id:
            # Every SET was guarded, so the stored row is untouched.
            return JobUpsertResult(
                outcome=JobUpsertOutcome.COMPANY_CONFLICT,
                source=str(job.source),
                source_job_id=job.source_job_id,
                persisted_company_id=int(record.company_id),
                incoming_company_id=company_id,
            )

        if record.previous_content_hash is None:
            outcome = JobUpsertOutcome.INSERTED
        elif record.previous_content_hash == incoming_hash:
            outcome = JobUpsertOutcome.UNCHANGED
        else:
            outcome = JobUpsertOutcome.UPDATED

        return JobUpsertResult(
            outcome=outcome,
            source=str(job.source),
            source_job_id=job.source_job_id,
            job_id=int(record.id),
        )
