"""What the ingestion service needs from persistence, and nothing more.

These Protocols list only the repository methods the service calls. The real
repositories satisfy them structurally, through the SQLAlchemy adapter in
``job_radar.persistence.ingestion_store``; unit tests satisfy them with an
in-memory fake. Either way the service never sees a session.

Errors: every method may raise ``PersistenceError`` and nothing else from
the database. Translating SQLAlchemy failures is the adapter's job
(ADR-0016 addendum).
"""

from collections.abc import Iterable, Sequence
from contextlib import AbstractContextManager
from datetime import datetime
from typing import Protocol

from job_radar.domain.crawl import CompanyCrawlStatus, CrawlRunStatus, JobCounters
from job_radar.domain.ingestion import JobUpsertResult
from job_radar.domain.job import NormalizedJob


class StoredCompany(Protocol):
    """A configured company as reconciliation left it."""

    @property
    def id(self) -> int: ...

    @property
    def source(self) -> str: ...

    @property
    def board_token(self) -> str: ...

    @property
    def display_name(self) -> str: ...


class CompanyStore(Protocol):
    def sync(
        self, *, source: str, board_token: str, display_name: str, enabled: bool
    ) -> int: ...

    def disable_absent(self, *, source: str, board_tokens: Iterable[str]) -> int: ...

    def enabled_companies(self) -> Sequence[StoredCompany]: ...


class JobStore(Protocol):
    def upsert(self, job: NormalizedJob, *, company_id: int) -> JobUpsertResult: ...


class CrawlStore(Protocol):
    def start(self, *, started_at: datetime) -> int: ...

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
    ) -> None: ...

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
    ) -> int: ...


class IngestionStore(Protocol):
    """The repositories bound to one transaction."""

    @property
    def companies(self) -> CompanyStore: ...

    @property
    def jobs(self) -> JobStore: ...

    @property
    def crawls(self) -> CrawlStore: ...


class StoreFactory(Protocol):
    """Opens one transaction.

    Leaving the block normally commits; leaving it with an exception rolls
    back and lets that exception propagate. A failed commit raises
    ``PersistenceError``. ``operation`` names the phase for error reporting.
    """

    def __call__(self, *, operation: str) -> AbstractContextManager[IngestionStore]: ...
