"""What a crawl run reports back to its caller.

These mirror the persisted ``crawl_runs`` and ``crawl_company_results`` rows
(ADR-0008). They live in the ingestion package rather than the domain because
a company is identified here by its board token, a term the domain boundary
deliberately keeps out of the domain.
"""

from dataclasses import dataclass
from datetime import datetime

from job_radar.domain.crawl import (
    CompanyCrawlStatus,
    CrawlRunStatus,
    JobCounters,
)


@dataclass(frozen=True, slots=True)
class CompanyCrawlSummary:
    """One company's outcome within a run.

    A failed company carries zero counters: none of its writes committed
    (SPEC, "Crawl outcome model").
    """

    company_id: int
    source: str
    board_token: str
    display_name: str
    status: CompanyCrawlStatus
    started_at: datetime
    finished_at: datetime
    counters: JobCounters
    #: ``CollectorError`` or ``PersistenceError``; only set when failed.
    error_type: str | None = None
    #: The error's safe message; only set when failed.
    error_message: str | None = None


@dataclass(frozen=True, slots=True)
class CrawlRunSummary:
    """A finished run, as recorded in ``crawl_runs``."""

    run_id: int
    status: CrawlRunStatus
    #: The run's single observation instant; also its ``started_at``.
    observed_at: datetime
    finished_at: datetime
    companies: tuple[CompanyCrawlSummary, ...]
    totals: JobCounters

    @property
    def companies_succeeded(self) -> int:
        return sum(1 for c in self.companies if c.status is CompanyCrawlStatus.SUCCESS)

    @property
    def companies_failed(self) -> int:
        return sum(1 for c in self.companies if c.status is CompanyCrawlStatus.FAILED)
