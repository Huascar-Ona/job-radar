"""Persisting crawl outcomes (ADR-0008).

Outcomes are durable state, not log lines, because deferred deactivation may
only reason about a company's absence after a crawl that *succeeded*
(ADR-0004). `last_successful_crawl` is the query that will need.
"""

from datetime import datetime
from typing import cast

from sqlalchemy import text
from sqlalchemy.orm import Session

from job_radar.domain.crawl import CompanyCrawlStatus, CrawlRunStatus, JobCounters

_START = text("""
INSERT INTO crawl_runs (status, started_at)
VALUES (:status, :started_at)
RETURNING id
""")

_FINISH = text("""
UPDATE crawl_runs SET
    status              = :status,
    finished_at         = :finished_at,
    companies_total     = :companies_total,
    companies_succeeded = :companies_succeeded,
    companies_failed    = :companies_failed,
    jobs_fetched        = :fetched,
    jobs_new            = :new,
    jobs_existing       = :existing,
    jobs_updated        = :updated,
    jobs_invalid        = :invalid
WHERE id = :run_id
""")

_RECORD_COMPANY = text("""
INSERT INTO crawl_company_results (
    crawl_run_id, company_id, status, started_at, finished_at,
    jobs_fetched, jobs_new, jobs_existing, jobs_updated, jobs_invalid,
    error_type, error_message
) VALUES (
    :run_id, :company_id, :status, :started_at, :finished_at,
    :fetched, :new, :existing, :updated, :invalid,
    :error_type, :error_message
)
RETURNING id
""")

_LAST_SUCCESS = text("""
SELECT max(started_at)
FROM crawl_company_results
WHERE company_id = :company_id AND status = :status
""")


class CrawlRepository:
    """Records crawl runs and their per-company outcomes. Never commits."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def start(self, *, started_at: datetime) -> int:
        """Open a run in the `running` state, returning its id."""
        return int(
            self._session.execute(
                _START,
                {"status": str(CrawlRunStatus.RUNNING), "started_at": started_at},
            ).scalar_one()
        )

    def finish(
        self,
        run_id: int,
        *,
        status: CrawlRunStatus,
        finished_at: datetime,
        companies_total: int,
        companies_succeeded: int,
        companies_failed: int,
        counters: JobCounters,
    ) -> None:
        """Close a run with its terminal status and aggregated counters."""
        self._session.execute(
            _FINISH,
            {
                "run_id": run_id,
                "status": str(status),
                "finished_at": finished_at,
                "companies_total": companies_total,
                "companies_succeeded": companies_succeeded,
                "companies_failed": companies_failed,
                "fetched": counters.fetched,
                "new": counters.new,
                "existing": counters.existing,
                "updated": counters.updated,
                "invalid": counters.invalid,
            },
        )

    def record_company(
        self,
        run_id: int,
        *,
        company_id: int,
        status: CompanyCrawlStatus,
        started_at: datetime,
        finished_at: datetime,
        counters: JobCounters,
        error_type: str | None = None,
        error_message: str | None = None,
    ) -> int:
        """Record one company's outcome within a run."""
        return int(
            self._session.execute(
                _RECORD_COMPANY,
                {
                    "run_id": run_id,
                    "company_id": company_id,
                    "status": str(status),
                    "started_at": started_at,
                    "finished_at": finished_at,
                    "fetched": counters.fetched,
                    "new": counters.new,
                    "existing": counters.existing,
                    "updated": counters.updated,
                    "invalid": counters.invalid,
                    "error_type": error_type,
                    "error_message": error_message,
                },
            ).scalar_one()
        )

    def last_successful_crawl(self, company_id: int) -> datetime | None:
        """When this company was last crawled successfully, if ever.

        Deferred deactivation must consult this before concluding that a job
        has disappeared: absence during a failed crawl means nothing.
        """
        # scalar_one() is untyped upstream; the column is timestamptz NULL.
        return cast(
            "datetime | None",
            self._session.execute(
                _LAST_SUCCESS,
                {"company_id": company_id, "status": str(CompanyCrawlStatus.SUCCESS)},
            ).scalar_one(),
        )
