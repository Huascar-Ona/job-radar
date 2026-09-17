"""Domain errors.

Failure isolation depends on a single catchable base class, and on failures
carrying enough context to identify source, company and operation
(SPEC, "Failure isolation").
"""


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
