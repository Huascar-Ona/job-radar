"""The ingestion service's view of the database, with failures made safe.

This adapter is the one place SQLAlchemy failures become ``PersistenceError``
(ARCHITECTURE, "Transaction ownership"; ADR-0016 addendum). It owns commit and
rollback for each transaction the ingestion service opens; the repositories
it wraps still never commit.

Error safety. SQLAlchemy and driver exception text can contain SQL, bound
parameters and row content, ``raw_data`` included, even in its first line.
So:

- the safe error is built from the exception's class name and, when the
  driver supplies one, its SQLSTATE code, and from nothing else;
- every translation happens where the database call is made (opening the
  session, each repository call, the commit), and the safe
  error is raised *after* the ``except`` block has ended, so the original is
  neither its ``__cause__`` nor its ``__context__``;
- the original exception is not logged.
"""

from collections.abc import Callable, Collection, Iterable, Sequence
from contextlib import suppress
from datetime import datetime
from types import TracebackType
from typing import Final

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from job_radar.domain.crawl import CompanyCrawlStatus, CrawlRunStatus, JobCounters
from job_radar.domain.errors import PersistenceError
from job_radar.domain.ingestion import JobUpsertResult
from job_radar.domain.job import NormalizedJob
from job_radar.persistence.company_repository import CompanyRecord, CompanyRepository
from job_radar.persistence.crawl_repository import CrawlRepository
from job_radar.persistence.job_repository import JobRepository

_COMMIT: Final = "commit"


def to_persistence_error(exc: SQLAlchemyError, *, operation: str) -> PersistenceError:
    """A safe stand-in for ``exc``: its class name and SQLSTATE, nothing more.

    ``str(exc)``, ``exc.args``, ``exc.statement``, ``exc.params`` and the
    driver message are deliberately never read.
    """
    driver_error = getattr(exc, "orig", None)
    sqlstate = getattr(driver_error, "sqlstate", None)
    return PersistenceError(
        operation=operation,
        category=type(exc).__name__,
        sqlstate=sqlstate if isinstance(sqlstate, str) else None,
    )


class _Guard:
    """Runs one database call, translating any SQLAlchemy failure."""

    def __init__(self, operation: str) -> None:
        self.operation = operation

    def call[T](self, fn: Callable[[], T]) -> T:
        try:
            return fn()
        except SQLAlchemyError as exc:
            error = to_persistence_error(exc, operation=self.operation)
        # Raised here, outside the except block, so the original exception is
        # not attached as __context__.
        raise error


class _Companies:
    def __init__(self, repository: CompanyRepository, guard: _Guard) -> None:
        self._repository = repository
        self._guard = guard

    def sync(
        self, *, source: str, board_token: str, display_name: str, enabled: bool
    ) -> int:
        return self._guard.call(
            lambda: self._repository.sync(
                source=source,
                board_token=board_token,
                display_name=display_name,
                enabled=enabled,
            )
        )

    def disable_absent(self, *, source: str, board_tokens: Iterable[str]) -> int:
        tokens = list(board_tokens)
        return self._guard.call(
            lambda: self._repository.disable_absent(source=source, board_tokens=tokens)
        )

    def enabled_companies(self) -> Sequence[CompanyRecord]:
        return self._guard.call(self._repository.enabled_companies)


class _Jobs:
    def __init__(self, repository: JobRepository, guard: _Guard) -> None:
        self._repository = repository
        self._guard = guard

    def upsert(self, job: NormalizedJob, *, company_id: int) -> JobUpsertResult:
        return self._guard.call(
            lambda: self._repository.upsert(job, company_id=company_id)
        )

    def count_active(self, company_id: int) -> int:
        return self._guard.call(lambda: self._repository.count_active(company_id))

    def count_absent(self, company_id: int, *, present: Collection[str]) -> int:
        return self._guard.call(
            lambda: self._repository.count_absent(company_id, present=present)
        )

    def deactivate_absent(
        self, company_id: int, *, present: Collection[str], deactivated_at: datetime
    ) -> int:
        return self._guard.call(
            lambda: self._repository.deactivate_absent(
                company_id, present=present, deactivated_at=deactivated_at
            )
        )


class _Crawls:
    def __init__(self, repository: CrawlRepository, guard: _Guard) -> None:
        self._repository = repository
        self._guard = guard

    def start(self, *, started_at: datetime) -> int:
        return self._guard.call(lambda: self._repository.start(started_at=started_at))

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
        self._guard.call(
            lambda: self._repository.finish(
                run_id,
                status=status,
                finished_at=finished_at,
                companies_total=companies_total,
                companies_succeeded=companies_succeeded,
                companies_failed=companies_failed,
                counters=counters,
            )
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
        completeness_verified: bool | None = None,
    ) -> int:
        return self._guard.call(
            lambda: self._repository.record_company(
                run_id,
                company_id=company_id,
                status=status,
                started_at=started_at,
                finished_at=finished_at,
                counters=counters,
                error_type=error_type,
                error_message=error_message,
                completeness_verified=completeness_verified,
            )
        )


class SqlAlchemyIngestionStore:
    """The three repositories bound to one session, every call guarded."""

    def __init__(self, session: Session, *, operation: str) -> None:
        guard = _Guard(operation)
        self.companies = _Companies(CompanyRepository(session), guard)
        self.jobs = _Jobs(JobRepository(session), guard)
        self.crawls = _Crawls(CrawlRepository(session), guard)


class SqlAlchemyTransaction:
    """One transaction: commit on a clean exit, roll back on any exception.

    A SQLAlchemy failure while opening the session raises ``PersistenceError``
    under the transaction's own operation name; any other exception from the
    session factory is a programming error and propagates unchanged.
    An exception leaving the block propagates unchanged after the rollback.
    A failed commit is rolled back and raised as ``PersistenceError``.
    Rollback and close failures are suppressed: the session is being
    discarded either way, and the error that matters is already in flight.
    """

    def __init__(
        self, session_factory: Callable[[], Session], *, operation: str
    ) -> None:
        self._session_factory = session_factory
        self._operation = operation
        self._session: Session | None = None

    def __enter__(self) -> SqlAlchemyIngestionStore:
        # Guarded like every other database call. If opening fails there is
        # no session, and __exit__ is not called: nothing to roll back.
        self._session = _Guard(self._operation).call(self._session_factory)
        return SqlAlchemyIngestionStore(self._session, operation=self._operation)

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        session = self._session
        if session is None:  # pragma: no cover - __enter__ always ran
            return
        self._session = None
        try:
            if exc is not None:
                _rollback_quietly(session)
                return
            error = _commit(session, operation=self._operation)
            if error is not None:
                _rollback_quietly(session)
                raise error
        finally:
            with suppress(SQLAlchemyError):
                session.close()


class SqlAlchemyStoreFactory:
    """Production ``StoreFactory``: one session per transaction."""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def __call__(self, *, operation: str) -> SqlAlchemyTransaction:
        return SqlAlchemyTransaction(self._session_factory, operation=operation)


def _commit(session: Session, *, operation: str) -> PersistenceError | None:
    try:
        session.commit()
    except SQLAlchemyError as exc:
        return to_persistence_error(exc, operation=f"{operation}_{_COMMIT}")
    return None


def _rollback_quietly(session: Session) -> None:
    with suppress(SQLAlchemyError):
        session.rollback()
