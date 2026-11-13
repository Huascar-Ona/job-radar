"""In-memory doubles for the ingestion service's dependencies.

The fake database models only what the service can observe: transactions
that commit or roll back as a whole, upsert outcomes classified the way
``JobRepository`` classifies them, and injectable ``PersistenceError``
failures. Real SQL behavior is covered by the PostgreSQL integration suite.
"""

import copy
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from types import TracebackType

from job_radar.collectors.base import FetchedBoard
from job_radar.config.boards import BoardConfig, BoardsConfig
from job_radar.domain.crawl import CompanyCrawlStatus, CrawlRunStatus, JobCounters
from job_radar.domain.errors import NormalizationError, PersistenceError
from job_radar.domain.ingestion import JobUpsertOutcome, JobUpsertResult
from job_radar.domain.job import JobSource, NormalizedJob

GREENHOUSE = "greenhouse"


@dataclass
class FakeCompany:
    id: int
    source: str
    board_token: str
    display_name: str
    enabled: bool


@dataclass
class FakeJobRow:
    company_id: int
    content: dict[str, object]
    first_seen_at: datetime
    last_seen_at: datetime
    is_active: bool = True
    deactivated_at: datetime | None = None


@dataclass
class FakeRun:
    status: CrawlRunStatus
    started_at: datetime
    finished_at: datetime | None = None
    companies_total: int | None = None
    companies_succeeded: int | None = None
    companies_failed: int | None = None
    counters: JobCounters | None = None


@dataclass
class FakeResult:
    run_id: int
    company_id: int
    status: CompanyCrawlStatus
    started_at: datetime
    finished_at: datetime
    counters: JobCounters
    error_type: str | None
    error_message: str | None
    completeness_verified: bool | None = None


@dataclass
class State:
    companies: dict[tuple[str, str], FakeCompany] = field(default_factory=dict)
    jobs: dict[tuple[str, str], FakeJobRow] = field(default_factory=dict)
    runs: dict[int, FakeRun] = field(default_factory=dict)
    results: list[FakeResult] = field(default_factory=list)
    next_id: int = 1

    def new_id(self) -> int:
        self.next_id += 1
        return self.next_id - 1


@dataclass
class TxRecord:
    operation: str
    calls: list[str] = field(default_factory=list)
    outcome: str | None = None  # "committed" | "rolled_back"


#: (method, operation, arguments) -> raise a PersistenceError?
FailureRule = Callable[[str, str, Mapping[str, object]], bool]


def persistence_failure(operation: str) -> PersistenceError:
    return PersistenceError(
        operation=operation, category="OperationalError", sqlstate="08006"
    )


class FakeDatabase:
    """A StoreFactory whose transactions copy state in and commit it back."""

    def __init__(self) -> None:
        self.state = State()
        self.transactions: list[TxRecord] = []
        self.open_transactions = 0
        self.fail: FailureRule = lambda _method, _operation, _args: False

    def __call__(self, *, operation: str) -> "FakeTransaction":
        return FakeTransaction(self, operation)

    def fail_on(
        self,
        method: str,
        *,
        operation: str | None = None,
        when: Callable[[Mapping[str, object]], bool] = lambda _args: True,
    ) -> None:
        def rule(m: str, op: str, args: Mapping[str, object]) -> bool:
            return m == method and (operation is None or op == operation) and when(args)

        self.fail = rule

    def operations(self) -> list[str]:
        return [tx.operation for tx in self.transactions]

    def seed_company(
        self, board_token: str, display_name: str, *, enabled: bool = True
    ) -> FakeCompany:
        company = FakeCompany(
            id=self.state.new_id(),
            source=GREENHOUSE,
            board_token=board_token,
            display_name=display_name,
            enabled=enabled,
        )
        self.state.companies[(company.source, board_token)] = company
        return company


class FakeTransaction:
    def __init__(self, db: FakeDatabase, operation: str) -> None:
        self._db = db
        self._record = TxRecord(operation)
        self.work = State()

    def __enter__(self) -> "FakeStore":
        self._db.open_transactions += 1
        self._db.transactions.append(self._record)
        self.work = copy.deepcopy(self._db.state)
        return FakeStore(self)

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._db.open_transactions -= 1
        if exc is not None:
            self._record.outcome = "rolled_back"
            return
        if self._db.fail("commit", self._record.operation, {}):
            self._record.outcome = "rolled_back"
            raise persistence_failure(f"{self._record.operation}_commit")
        self._db.state = self.work
        self._record.outcome = "committed"

    def call(self, method: str, args: Mapping[str, object]) -> State:
        self._record.calls.append(method)
        if self._db.fail(method, self._record.operation, args):
            raise persistence_failure(self._record.operation)
        return self.work


class FakeStore:
    def __init__(self, tx: FakeTransaction) -> None:
        self.companies = _Companies(tx)
        self.jobs = _Jobs(tx)
        self.crawls = _Crawls(tx)


class _Companies:
    def __init__(self, tx: FakeTransaction) -> None:
        self._tx = tx

    def sync(
        self, *, source: str, board_token: str, display_name: str, enabled: bool
    ) -> int:
        state = self._tx.call("sync", {"board_token": board_token})
        existing = state.companies.get((source, board_token))
        if existing is None:
            existing = FakeCompany(
                state.new_id(), source, board_token, display_name, enabled
            )
            state.companies[(source, board_token)] = existing
        existing.display_name = display_name
        existing.enabled = enabled
        return existing.id

    def disable_absent(self, *, source: str, board_tokens: Iterable[str]) -> int:
        tokens = set(board_tokens)
        state = self._tx.call("disable_absent", {"source": source})
        disabled = 0
        for company in state.companies.values():
            if (
                company.source == source
                and company.enabled
                and company.board_token not in tokens
            ):
                company.enabled = False
                disabled += 1
        return disabled

    def enabled_companies(self) -> Sequence[FakeCompany]:
        state = self._tx.call("enabled_companies", {})
        return [copy.copy(c) for _, c in sorted(state.companies.items()) if c.enabled]


class _Jobs:
    def __init__(self, tx: FakeTransaction) -> None:
        self._tx = tx

    def upsert(self, job: NormalizedJob, *, company_id: int) -> JobUpsertResult:
        state = self._tx.call(
            "upsert", {"source_job_id": job.source_job_id, "company_id": company_id}
        )
        key = (str(job.source), job.source_job_id)
        content = job.model_dump(mode="json", exclude={"observed_at"})
        row = state.jobs.get(key)
        source = str(job.source)
        if row is None:
            state.jobs[key] = FakeJobRow(
                company_id, content, job.observed_at, job.observed_at
            )
            outcome = JobUpsertOutcome.INSERTED
        elif row.company_id != company_id:
            return JobUpsertResult(
                outcome=JobUpsertOutcome.COMPANY_CONFLICT,
                source=source,
                source_job_id=job.source_job_id,
                persisted_company_id=row.company_id,
                incoming_company_id=company_id,
            )
        elif row.content == content:
            row.last_seen_at = job.observed_at
            outcome = JobUpsertOutcome.UNCHANGED
        else:
            row.content = content
            row.last_seen_at = job.observed_at
            outcome = JobUpsertOutcome.UPDATED
        if row is not None:
            # Seen again: active again (ADR-0017), like the real upsert.
            row.is_active = True
            row.deactivated_at = None
        return JobUpsertResult(
            outcome=outcome, source=source, source_job_id=job.source_job_id, job_id=1
        )

    def _absent(
        self, state: State, company_id: int, present: Collection[str]
    ) -> list[FakeJobRow]:
        if not present:
            msg = "present ids must not be empty"
            raise ValueError(msg)
        return [
            row
            for (_, job_id), row in sorted(state.jobs.items())
            if row.company_id == company_id and row.is_active and job_id not in present
        ]

    def count_active(self, company_id: int) -> int:
        state = self._tx.call("count_active", {"company_id": company_id})
        return sum(
            1
            for row in state.jobs.values()
            if row.company_id == company_id and row.is_active
        )

    def count_absent(self, company_id: int, *, present: Collection[str]) -> int:
        state = self._tx.call("count_absent", {"company_id": company_id})
        return len(self._absent(state, company_id, present))

    def deactivate_absent(
        self, company_id: int, *, present: Collection[str], deactivated_at: datetime
    ) -> int:
        state = self._tx.call("deactivate_absent", {"company_id": company_id})
        absent = self._absent(state, company_id, present)
        for row in absent:
            row.is_active = False
            row.deactivated_at = deactivated_at
        return len(absent)


class _Crawls:
    def __init__(self, tx: FakeTransaction) -> None:
        self._tx = tx

    def start(self, *, started_at: datetime) -> int:
        state = self._tx.call("start", {})
        run_id = state.new_id()
        state.runs[run_id] = FakeRun(CrawlRunStatus.RUNNING, started_at)
        return run_id

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
        state = self._tx.call("finish", {"run_id": run_id})
        run = state.runs[run_id]
        run.status = status
        run.finished_at = finished_at
        run.companies_total = companies_total
        run.companies_succeeded = companies_succeeded
        run.companies_failed = companies_failed
        run.counters = counters

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
        state = self._tx.call(
            "record_company", {"company_id": company_id, "status": status}
        )
        state.results.append(
            FakeResult(
                run_id,
                company_id,
                status,
                started_at,
                finished_at,
                counters,
                error_type,
                error_message,
                completeness_verified,
            )
        )
        return len(state.results)


# --- collector and normalizer ----------------------------------------------


class FakeCollector:
    """Serves canned postings per board token, or raises a canned exception.

    A plain sequence is served as a board whose completeness is verified; a
    ``FetchedBoard`` is served as given.
    """

    def __init__(
        self,
        db: FakeDatabase,
        boards: Mapping[str, Sequence[object] | FetchedBoard | Exception],
    ) -> None:
        self._db = db
        self.boards = dict(boards)
        self.calls: list[str] = []
        self.open_transactions_during_fetch: list[int] = []

    def fetch_jobs(self, *, board_token: str) -> FetchedBoard:
        self.calls.append(board_token)
        self.open_transactions_during_fetch.append(self._db.open_transactions)
        response = self.boards[board_token]
        if isinstance(response, Exception):
            raise response
        if isinstance(response, FetchedBoard):
            return response
        return FetchedBoard(postings=list(response), completeness_verified=True)


class FakeNormalizer:
    """Turns ``{"id": ..., "title": ...}`` into a NormalizedJob.

    ``{"invalid": True}`` raises NormalizationError; ``{"bug": True}`` raises
    RuntimeError, standing in for a programming error.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[object, str, datetime]] = []

    def normalize(
        self, posting: object, *, company: str, observed_at: datetime
    ) -> NormalizedJob:
        self.calls.append((posting, company, observed_at))
        if not isinstance(posting, dict) or "id" not in posting:
            raise NormalizationError(
                source=GREENHOUSE, source_job_id=None, reason="posting has no id"
            )
        job_id = str(posting["id"])
        if posting.get("bug"):
            msg = "simulated programming error"
            raise RuntimeError(msg)
        if posting.get("invalid"):
            raise NormalizationError(
                source=GREENHOUSE, source_job_id=job_id, reason="missing title"
            )
        return NormalizedJob(
            source=JobSource.GREENHOUSE,
            source_job_id=job_id,
            company=company,
            title=str(posting.get("title", "Engineer")),
            job_url=f"https://jobs.example.test/{job_id}",
            observed_at=observed_at,
            raw_data=dict(posting),
        )


def posting(job_id: int | str, **fields: object) -> dict[str, object]:
    return {"id": job_id, **fields}


def boards(*entries: tuple[str, str] | tuple[str, str, bool]) -> BoardsConfig:
    """``("token", "Display")`` or ``("token", "Display", enabled)``."""
    return BoardsConfig(
        boards=tuple(
            BoardConfig(
                source=JobSource.GREENHOUSE,
                board_token=entry[0],
                display_name=entry[1],
                enabled=entry[2] if len(entry) == 3 else True,
            )
            for entry in entries
        )
    )
