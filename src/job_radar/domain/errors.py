"""Domain errors.

Failure isolation depends on a single catchable base class, and on failures
carrying enough context to identify source, company and operation
(SPEC, "Failure isolation").
"""

import re
from typing import Final


class JobRadarError(Exception):
    """Base class for every error this application raises deliberately."""


class CollectorError(JobRadarError):
    """A source could not be fetched.

    Raised by collectors; caught by the ingestion service, which records the
    company as failed and continues with the next one. The failure must never
    touch that company's stored jobs.
    """

    def __init__(
        self, *, source: str, company: str, operation: str, message: str
    ) -> None:
        self.source = source
        self.company = company
        self.operation = operation
        self.message = message
        super().__init__(f"[{source}:{company}] {operation} failed: {message}")

    def log_context(self) -> dict[str, str]:
        """The structured-log fields SPEC requires for an isolated failure."""
        return {
            "source": self.source,
            "company": self.company,
            "operation": self.operation,
            "error": self.message,
        }


class NormalizationError(JobRadarError):
    """A payload entry could not be turned into a NormalizedJob.

    One rejected entry is counted as ``invalid`` and must not abort the rest
    of the batch.
    """

    def __init__(self, *, source: str, source_job_id: str | None, reason: str) -> None:
        self.source = source
        self.source_job_id = source_job_id
        self.reason = reason
        identifier = source_job_id if source_job_id is not None else "<unknown id>"
        super().__init__(f"[{source}:{identifier}] cannot normalize: {reason}")

    def log_context(self) -> dict[str, str | None]:
        """Structured-log fields for a rejected entry."""
        return {
            "source": self.source,
            "source_job_id": self.source_job_id,
            "operation": "normalize",
            "error": self.reason,
        }


#: A SQLAlchemy exception class name: an identifier, never free text.
_CATEGORY: Final = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")
#: SQLSTATE is five characters of digits and upper-case letters (SQL standard).
_SQLSTATE: Final = re.compile(r"[0-9A-Z]{5}")
#: Operation names are chosen by Job Radar code, never taken from a database.
_OPERATION: Final = re.compile(r"[a-z][a-z0-9_]{0,63}")
_FALLBACK_CATEGORY: Final = "DatabaseError"
_FALLBACK_OPERATION: Final = "persistence"


class PersistenceError(JobRadarError):
    """The database could not complete a write or read ingestion needed.

    Raised by the persistence adapter in place of every SQLAlchemy failure,
    so the ingestion service never depends on SQLAlchemy (ADR-0016 addendum).

    Driver and SQLAlchemy exception text can carry SQL, bound parameters and
    row content, ``raw_data`` included, even in its first line. This class
    therefore accepts no free text at all: its message is built only from
    the exception category (a class name) and an optional SQLSTATE code, and
    anything that does not look like one of those is replaced, not quoted.
    The result is short and bounded by construction.
    """

    def __init__(
        self, *, operation: str, category: str, sqlstate: str | None = None
    ) -> None:
        self.operation = (
            operation if _OPERATION.fullmatch(operation) else _FALLBACK_OPERATION
        )
        self.category = (
            category if _CATEGORY.fullmatch(category) else _FALLBACK_CATEGORY
        )
        self.sqlstate = (
            sqlstate if sqlstate is not None and _SQLSTATE.fullmatch(sqlstate) else None
        )
        self.message = (
            self.category
            if self.sqlstate is None
            else f"{self.category} (SQLSTATE {self.sqlstate})"
        )
        super().__init__(f"{self.operation} failed: {self.message}")

    def log_context(self) -> dict[str, str]:
        """Structured-log fields; every value is safe by construction."""
        return {"operation": self.operation, "error": self.message}
