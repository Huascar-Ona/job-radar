"""Outcomes of persisting one observation.

The repository reports what happened as a value rather than by raising, so the
ingestion service can count outcomes without using exceptions for control flow.
"""

from dataclasses import dataclass
from enum import StrEnum


class JobUpsertOutcome(StrEnum):
    """What persisting one observation did."""

    #: The job was not known before. Counts as `new`.
    INSERTED = "inserted"
    #: Already known, nothing about the source data changed. Counts as
    #: `existing` but not `updated`; only last_seen_at moved.
    UNCHANGED = "unchanged"
    #: Already known and the source data changed. Counts as `existing` and
    #: `updated`.
    UPDATED = "updated"
    #: Already known under a *different* company. Nothing was written.
    COMPANY_CONFLICT = "company_conflict"


@dataclass(frozen=True, slots=True)
class JobUpsertResult:
    """The outcome of one upsert, with the context needed to log an anomaly."""

    outcome: JobUpsertOutcome
    source: str
    source_job_id: str
    #: Present for every outcome except COMPANY_CONFLICT, where nothing was written.
    job_id: int | None = None
    #: Company the stored row belongs to. Only set on COMPANY_CONFLICT.
    persisted_company_id: int | None = None
    #: Company this observation claimed. Only set on COMPANY_CONFLICT.
    incoming_company_id: int | None = None

    @property
    def is_anomaly(self) -> bool:
        """Whether this outcome needs operator attention rather than a counter."""
        return self.outcome is JobUpsertOutcome.COMPANY_CONFLICT

    @property
    def is_new(self) -> bool:
        return self.outcome is JobUpsertOutcome.INSERTED

    @property
    def is_existing(self) -> bool:
        return self.outcome in (JobUpsertOutcome.UNCHANGED, JobUpsertOutcome.UPDATED)

    @property
    def is_updated(self) -> bool:
        return self.outcome is JobUpsertOutcome.UPDATED

    def log_context(self) -> dict[str, str | int | None]:
        """Structured-log fields describing this outcome."""
        return {
            "source": self.source,
            "source_job_id": self.source_job_id,
            "operation": "upsert_job",
            "outcome": str(self.outcome),
            "error": (
                "job identity already belongs to a different company"
                if self.is_anomaly
                else None
            ),
            "persisted_company_id": self.persisted_company_id,
            "incoming_company_id": self.incoming_company_id,
        }
