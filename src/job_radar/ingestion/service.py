"""The ingestion run: reconcile, crawl every enabled company, record outcomes.

Implements SPEC "Ingestion run", "Failure isolation", "Crawl outcome model"
and "Run reporting", and ARCHITECTURE "Transaction ownership". In short:

1. Configuration is reconciled in its own transaction, which also checks
   that every enabled company's source is wired before it commits.
2. ``observed_at`` is read once from the clock, and the run row is opened in
   the ``running`` state and committed.
3. Companies are crawled one at a time. Each is fetched **outside** any
   transaction, then, in one transaction: its active jobs are counted, its
   postings are normalized and upserted in response order, its absent jobs
   are deactivated when every ADR-0017 guard holds, and its success record
   is written.
4. The run row is finished with its status and totals.

A ``CollectorError`` or ``PersistenceError`` fails one company: its
transaction rolls back and a failure record with zero counters is written in
a new transaction. If that record cannot be written, the run aborts and may
stay ``running``. Any other exception is a programming error and propagates.

No retries and no concurrency (ADR-0016, ADR-0009). Deactivation only on
verified absence (ADR-0017).
"""

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Final

from job_radar.collectors.base import Collector, Normalizer
from job_radar.config.boards import BoardsConfig
from job_radar.domain.clock import Clock
from job_radar.domain.crawl import (
    CompanyCrawlStatus,
    JobCounters,
    derive_run_status,
)
from job_radar.domain.errors import CollectorError, NormalizationError, PersistenceError
from job_radar.domain.ingestion import JobUpsertOutcome
from job_radar.domain.job import JobSource
from job_radar.ingestion.deactivation import (
    CrawlFacts,
    SkipReason,
    canonical_source_job_id,
    is_large_disappearance,
    precheck,
)
from job_radar.ingestion.ports import IngestionStore, StoredCompany, StoreFactory
from job_radar.ingestion.summary import CompanyCrawlSummary, CrawlRunSummary

LOGGER_NAME: Final = "job_radar.ingestion"

#: Upper bound on a persisted or logged error message. A backstop only: every
#: error message is already safe and short by construction (SPEC, "Safe error
#: messages"), so this never sanitizes, it only caps.
MAX_ERROR_MESSAGE_CHARS: Final = 500

# Operation names: the phase a PersistenceError is reported against.
OP_RECONCILE: Final = "reconcile_config"
OP_START_RUN: Final = "start_run"
OP_PERSIST_COMPANY: Final = "persist_company"
OP_RECORD_FAILURE: Final = "record_company_failure"
OP_FINISH_RUN: Final = "finish_run"
#: Log operation for deactivation records; a database failure while
#: deactivating is reported under OP_PERSIST_COMPANY, like any write of the
#: company's transaction.
OP_DEACTIVATE: Final = "deactivate_jobs"

#: The failures that fail one company rather than the run (SPEC, "Failure
#: classes").
_RECOVERABLE: Final = (CollectorError, PersistenceError)


@dataclass(slots=True)
class _Tally:
    """Mutable per-company counting; frozen into JobCounters at the end."""

    fetched: int = 0
    new: int = 0
    existing: int = 0
    updated: int = 0
    invalid: int = 0

    def freeze(self) -> JobCounters:
        return JobCounters(
            fetched=self.fetched,
            new=self.new,
            existing=self.existing,
            updated=self.updated,
            invalid=self.invalid,
        )


@dataclass(slots=True)
class _Ingested:
    """What one company's upserts observed, for the deactivation guards."""

    counters: JobCounters = field(default_factory=JobCounters)
    #: Ids of this fetch: valid postings (conflicting ones included) and
    #: invalid postings whose id is canonical. A stored job outside it is
    #: absent (ADR-0017).
    present: set[str] = field(default_factory=set)
    #: Postings that normalized into a job.
    valid: int = 0
    #: Invalid postings whose id is unknown or not canonical.
    invalid_without_id: int = 0


class IngestionService:
    """Runs one crawl over every enabled company.

    Every dependency is injected. Source-specific collectors and normalizers
    are supplied per source by the caller, which is the only place a source
    is named.
    """

    def __init__(
        self,
        *,
        store: StoreFactory,
        collectors: Mapping[JobSource, Collector],
        normalizers: Mapping[JobSource, Normalizer],
        clock: Clock,
        logger: logging.Logger | None = None,
    ) -> None:
        if set(collectors) != set(normalizers):
            msg = (
                "collectors and normalizers must cover the same sources: "
                f"collectors={sorted(collectors)}, normalizers={sorted(normalizers)}"
            )
            raise ValueError(msg)
        self._store = store
        self._collectors = dict(collectors)
        self._normalizers = dict(normalizers)
        self._clock = clock
        self._log = logger if logger is not None else logging.getLogger(LOGGER_NAME)

    # -- the run -------------------------------------------------------------

    def run(self, boards: BoardsConfig) -> CrawlRunSummary:
        """Reconcile ``boards``, crawl every enabled company, record outcomes.

        Raises:
            PersistenceError: reconciliation, opening or finishing the run, or
                recording a failed company's result could not be persisted.
                The run row may be left ``running``.
            ValueError: an enabled company's source has no collector wired.
                Raised before reconciliation commits, so the configuration
                change is rolled back, no run is opened and nothing is fetched.
        """
        companies = self._reconcile(boards)

        observed_at = self._clock.now()
        with self._store(operation=OP_START_RUN) as store:
            run_id = store.crawls.start(started_at=observed_at)

        results = tuple(
            self._crawl_company(run_id, company, observed_at) for company in companies
        )
        return self._finish(run_id, observed_at, results)

    def _reconcile(self, boards: BoardsConfig) -> Sequence[StoredCompany]:
        with self._store(operation=OP_RECONCILE) as store:
            for board in boards.boards:
                store.companies.sync(
                    source=str(board.source),
                    board_token=board.board_token,
                    display_name=board.display_name,
                    enabled=board.enabled,
                )
            for source in JobSource:
                store.companies.disable_absent(
                    source=str(source),
                    board_tokens=[
                        b.board_token for b in boards.boards if b.source is source
                    ],
                )
            companies = tuple(store.companies.enabled_companies())
            # Inside the reconciliation transaction, so a wiring bug rolls the
            # configuration change back: it is not a crawl, and it must not
            # leave the companies table describing one that never ran.
            self._require_wiring(companies)
            return companies

    def _require_wiring(self, companies: Sequence[StoredCompany]) -> None:
        # Every enabled company is checked, not only the first.
        unwired = sorted(
            {c.source for c in companies} - {str(source) for source in self._collectors}
        )
        if unwired:
            msg = f"no collector configured for enabled sources: {unwired}"
            raise ValueError(msg)

    def _finish(
        self,
        run_id: int,
        observed_at: datetime,
        results: tuple[CompanyCrawlSummary, ...],
    ) -> CrawlRunSummary:
        totals = sum((r.counters for r in results), JobCounters())
        succeeded = sum(1 for r in results if r.status is CompanyCrawlStatus.SUCCESS)
        failed = len(results) - succeeded
        status = derive_run_status(succeeded=succeeded, failed=failed)
        finished_at = self._clock.now()
        with self._store(operation=OP_FINISH_RUN) as store:
            store.crawls.finish(
                run_id,
                status=status,
                finished_at=finished_at,
                companies_total=len(results),
                companies_succeeded=succeeded,
                companies_failed=failed,
                counters=totals,
            )

        self._log.info(
            "crawl run %s finished: %s",
            run_id,
            status,
            extra={
                "operation": "crawl_run",
                "run_id": run_id,
                "status": str(status),
                "companies_total": len(results),
                "companies_succeeded": succeeded,
                "companies_failed": failed,
                "fetched": totals.fetched,
                "new": totals.new,
                "existing": totals.existing,
                "updated": totals.updated,
                "invalid": totals.invalid,
                "deactivated": totals.deactivated,
            },
        )
        return CrawlRunSummary(
            run_id=run_id,
            status=status,
            observed_at=observed_at,
            finished_at=finished_at,
            companies=results,
            totals=totals,
        )

    # -- one company ---------------------------------------------------------

    def _crawl_company(
        self, run_id: int, company: StoredCompany, observed_at: datetime
    ) -> CompanyCrawlSummary:
        started_at = self._clock.now()
        source = JobSource(company.source)
        fetched: int | None = None
        failure: CollectorError | PersistenceError
        try:
            # Outside any transaction: no connection is held during HTTP.
            board = self._collectors[source].fetch_jobs(board_token=company.board_token)
            postings = board.postings
            fetched = len(postings)
            with self._store(operation=OP_PERSIST_COMPANY) as store:
                # Before any upsert: the jobs active before this crawl (E2).
                previously_active = store.jobs.count_active(company.id)
                ingested = self._ingest(store, company, postings, observed_at)
                deactivated = self._deactivate(
                    store,
                    run_id,
                    company,
                    ingested,
                    completeness_verified=board.completeness_verified,
                    previously_active=previously_active,
                    observed_at=observed_at,
                )
                counters = replace(ingested.counters, deactivated=deactivated)
                finished_at = self._clock.now()
                # Same transaction as the jobs and their deactivation: the
                # success record exists exactly when those writes committed
                # (ADR-0008 addendum, ADR-0017).
                store.crawls.record_company(
                    run_id,
                    company_id=company.id,
                    status=CompanyCrawlStatus.SUCCESS,
                    started_at=started_at,
                    finished_at=finished_at,
                    counters=counters,
                    completeness_verified=board.completeness_verified,
                )
            return CompanyCrawlSummary(
                company_id=company.id,
                source=company.source,
                board_token=company.board_token,
                display_name=company.display_name,
                status=CompanyCrawlStatus.SUCCESS,
                started_at=started_at,
                finished_at=finished_at,
                counters=counters,
                completeness_verified=board.completeness_verified,
            )
        except _RECOVERABLE as exc:
            failure = exc

        # Recorded outside the except block, so a failure here does not carry
        # the company's error along as its context.
        return self._record_failure(run_id, company, started_at, failure, fetched)

    def _ingest(
        self,
        store: IngestionStore,
        company: StoredCompany,
        postings: Sequence[object],
        observed_at: datetime,
    ) -> _Ingested:
        """Normalize and upsert every posting occurrence, in response order.

        Duplicate ids are not deduplicated: each occurrence is counted by
        what its own upsert reports (SPEC, "Run reporting"). Also collects
        the ids this fetch contained, for the deactivation guards.
        """
        normalizer = self._normalizers[JobSource(company.source)]
        tally = _Tally(fetched=len(postings))
        ingested = _Ingested()
        for posting in postings:
            try:
                job = normalizer.normalize(
                    posting, company=company.display_name, observed_at=observed_at
                )
            except NormalizationError as exc:
                tally.invalid += 1
                # A known, canonical id protects its stored job from
                # deactivation; an unknown one blocks it (ADR-0017).
                known = canonical_source_job_id(exc.source_job_id)
                if known is None:
                    ingested.invalid_without_id += 1
                else:
                    ingested.present.add(known)
                self._log.warning(
                    "posting rejected for %s: %s",
                    company.board_token,
                    exc.reason,
                    extra={
                        **_company_context(company),
                        "operation": "normalize",
                        "source_job_id": exc.source_job_id,
                        "error_type": type(exc).__name__,
                        "error": _bounded(exc.reason),
                    },
                )
                continue

            ingested.valid += 1
            ingested.present.add(job.source_job_id)
            result = store.jobs.upsert(job, company_id=company.id)
            match result.outcome:
                case JobUpsertOutcome.INSERTED:
                    tally.new += 1
                case JobUpsertOutcome.UNCHANGED:
                    tally.existing += 1
                case JobUpsertOutcome.UPDATED:
                    tally.existing += 1
                    tally.updated += 1
                case JobUpsertOutcome.COMPANY_CONFLICT:
                    # Counted as invalid so fetched = new + existing + invalid
                    # holds; the anomaly is visible here and only here
                    # (ADR-0016 addendum, "Counting").
                    tally.invalid += 1
                    self._log.error(
                        "job %s already belongs to company %s, not %s",
                        result.source_job_id,
                        result.persisted_company_id,
                        result.incoming_company_id,
                        extra={
                            **_company_context(company),
                            "operation": "upsert_job",
                            "source_job_id": result.source_job_id,
                            "outcome": str(result.outcome),
                            "persisted_company_id": result.persisted_company_id,
                            "incoming_company_id": result.incoming_company_id,
                            "error": "job identity already belongs to a "
                            "different company",
                        },
                    )
        ingested.counters = tally.freeze()
        return ingested

    def _deactivate(
        self,
        store: IngestionStore,
        run_id: int,
        company: StoredCompany,
        ingested: _Ingested,
        *,
        completeness_verified: bool,
        previously_active: int,
        observed_at: datetime,
    ) -> int:
        """Deactivate the company's absent jobs if every guard holds.

        Returns how many were deactivated. A skip is not a failure: it logs
        its reason and returns 0, and the company's upserts stand. Runs inside
        the company's transaction, so a database failure here rolls back the
        whole company (ADR-0017).
        """
        if previously_active == 0:
            # Nothing was active, so nothing can be absent: no guard, no log.
            return 0
        context: dict[str, object] = {
            **_company_context(company),
            "operation": OP_DEACTIVATE,
            "run_id": run_id,
            "previously_active": previously_active,
        }
        reason = precheck(
            CrawlFacts(
                completeness_verified=completeness_verified,
                fetched=ingested.counters.fetched,
                valid=ingested.valid,
                invalid_without_id=ingested.invalid_without_id,
            )
        )
        absent: int | None = None
        if reason is None:
            absent = store.jobs.count_absent(company.id, present=ingested.present)
            if is_large_disappearance(
                absent=absent, previously_active=previously_active
            ):
                reason = SkipReason.LARGE_DISAPPEARANCE

        if reason is not None:
            if absent is not None:
                context["absent"] = absent
            self._log.log(
                logging.WARNING
                if reason is SkipReason.LARGE_DISAPPEARANCE
                else logging.INFO,
                "deactivation skipped for %s: %s",
                company.board_token,
                reason,
                extra={**context, "deactivation_skipped": str(reason)},
            )
            return 0

        if not absent:
            return 0
        deactivated = store.jobs.deactivate_absent(
            company.id, present=ingested.present, deactivated_at=observed_at
        )
        self._log.info(
            "deactivated %d jobs of %s",
            deactivated,
            company.board_token,
            extra={**context, "absent": absent, "deactivated": deactivated},
        )
        return deactivated

    def _record_failure(
        self,
        run_id: int,
        company: StoredCompany,
        started_at: datetime,
        error: CollectorError | PersistenceError,
        fetched: int | None,
    ) -> CompanyCrawlSummary:
        error_type = type(error).__name__
        error_message = _bounded(error.message)
        self._log.error(
            "company %s failed during %s: %s",
            company.board_token,
            error.operation,
            error_message,
            extra={
                **_company_context(company),
                "operation": error.operation,
                "error_type": error_type,
                "error": error_message,
                # What was fetched before failing lives only here; the
                # persisted record carries zero counters (ADR-0008 addendum).
                "fetched": fetched,
            },
        )

        finished_at = self._clock.now()
        try:
            with self._store(operation=OP_RECORD_FAILURE) as store:
                store.crawls.record_company(
                    run_id,
                    company_id=company.id,
                    status=CompanyCrawlStatus.FAILED,
                    started_at=started_at,
                    finished_at=finished_at,
                    counters=JobCounters(),
                    error_type=error_type,
                    error_message=error_message,
                )
        except PersistenceError as record_error:
            self._log.critical(
                "could not record failure of %s; aborting run %s",
                company.board_token,
                run_id,
                extra={
                    **_company_context(company),
                    "operation": record_error.operation,
                    "error_type": type(record_error).__name__,
                    "error": record_error.message,
                    "run_id": run_id,
                },
            )
            raise

        return CompanyCrawlSummary(
            company_id=company.id,
            source=company.source,
            board_token=company.board_token,
            display_name=company.display_name,
            status=CompanyCrawlStatus.FAILED,
            started_at=started_at,
            finished_at=finished_at,
            counters=JobCounters(),
            error_type=error_type,
            error_message=error_message,
        )


def _company_context(company: StoredCompany) -> dict[str, object]:
    return {
        "source": company.source,
        "board_token": company.board_token,
        "display_name": company.display_name,
        "company_id": company.id,
    }


def _bounded(message: str) -> str:
    if len(message) <= MAX_ERROR_MESSAGE_CHARS:
        return message
    return message[: MAX_ERROR_MESSAGE_CHARS - 3] + "..."
