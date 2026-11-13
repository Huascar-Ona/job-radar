"""IngestionService orchestration against in-memory fakes.

No HTTP and no database: the collector, normalizer and store are fakes from
tests/support/ingestion_fakes.py and time is a FixedClock. What these tests
pin is orchestration (SPEC "Ingestion run", "Failure isolation", "Crawl
outcome model", "Run reporting"); real SQL behavior is the integration
suite's job.
"""

import copy
import logging
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta

import pytest

from job_radar.collectors.base import Collector, Normalizer
from job_radar.config.boards import BoardsConfig
from job_radar.domain.crawl import CompanyCrawlStatus, CrawlRunStatus, JobCounters
from job_radar.domain.errors import CollectorError, PersistenceError
from job_radar.domain.job import JobSource
from job_radar.ingestion.ports import StoreFactory
from job_radar.ingestion.service import (
    LOGGER_NAME,
    MAX_ERROR_MESSAGE_CHARS,
    IngestionService,
)
from job_radar.ingestion.summary import CompanyCrawlSummary, CrawlRunSummary
from tests.support.clock import FixedClock
from tests.support.ingestion_fakes import (
    GREENHOUSE,
    FakeCollector,
    FakeDatabase,
    FakeNormalizer,
    boards,
    posting,
)

T0 = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)


class SteppingClock(FixedClock):
    """A FixedClock that moves one second after every reading."""

    def __init__(self, moment: datetime) -> None:
        super().__init__(moment)
        self.readings = 0

    def now(self) -> datetime:
        moment = super().now()
        self.readings += 1
        self.advance(timedelta(seconds=1))
        return moment


class Harness:
    def __init__(
        self,
        responses: Mapping[str, Sequence[object] | Exception] | None = None,
        *,
        clock: FixedClock | None = None,
    ) -> None:
        self.db = FakeDatabase()
        self.collector = FakeCollector(self.db, responses or {})
        self.normalizer = FakeNormalizer()
        self.clock = clock if clock is not None else FixedClock(T0)
        self.service = IngestionService(
            store=self.db,
            collectors={JobSource.GREENHOUSE: self.collector},
            normalizers={JobSource.GREENHOUSE: self.normalizer},
            clock=self.clock,
        )

    def run(self, config: BoardsConfig) -> CrawlRunSummary:
        summary = self.service.run(config)
        assert_counter_invariants(summary)
        return summary


def assert_counter_invariants(summary: CrawlRunSummary) -> None:
    """SPEC "Run reporting": checked on every run these tests complete."""
    for company in summary.companies:
        c = company.counters
        assert c.fetched == c.new + c.existing + c.invalid, company
        assert c.updated <= c.existing, company
        if company.status is CompanyCrawlStatus.FAILED:
            assert c == JobCounters(), "a failed company records zero counters"
    assert summary.totals == sum(
        (company.counters for company in summary.companies), JobCounters()
    )


def by_token(summary: CrawlRunSummary, token: str) -> CompanyCrawlSummary:
    [match] = [c for c in summary.companies if c.board_token == token]
    return match


def records(caplog: pytest.LogCaptureFixture, level: int) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == LOGGER_NAME and r.levelno == level]


def field(record: logging.LogRecord, name: str) -> object:
    return record.__dict__[name]


# --- wiring -----------------------------------------------------------------


def test_fakes_satisfy_the_protocols() -> None:
    # Statically checked by mypy through these annotations, and at runtime by
    # the runtime_checkable protocols.
    h = Harness()
    store: StoreFactory = h.db
    collector: Collector = h.collector
    normalizer: Normalizer = h.normalizer

    assert store is h.db
    assert isinstance(collector, Collector)
    assert isinstance(normalizer, Normalizer)


def test_mismatched_collector_and_normalizer_sources_are_rejected() -> None:
    h = Harness()

    with pytest.raises(ValueError, match="same sources"):
        IngestionService(
            store=h.db,
            collectors={JobSource.GREENHOUSE: h.collector},
            normalizers={},
            clock=h.clock,
        )


def test_an_enabled_company_of_an_unwired_source_fails_before_the_run() -> None:
    h = Harness()
    h.db.seed_company("elsewhere", "Elsewhere").source = "lever"

    with pytest.raises(ValueError, match="lever"):
        h.service.run(boards())

    assert h.db.state.runs == {}, "a wiring bug must not open a crawl run"
    assert h.db.transactions[-1].outcome == "rolled_back"


def test_a_wired_company_followed_by_an_unwired_one_rolls_reconciliation_back() -> None:
    # Enabled companies come back ordered by (source, board_token), so the
    # wired greenhouse company is first and the unwired one second: checking
    # only the first would miss it.
    h = Harness({"alpha": [posting(1)], "gamma": [posting(2)]})
    h.db.seed_company("alpha", "Old Alpha")
    h.db.seed_company("beta", "Beta")  # absent from config: would be disabled
    h.db.seed_company("elsewhere", "Elsewhere").source = "lever"
    before = copy.deepcopy(h.db.state)

    with pytest.raises(ValueError, match=r"\['lever'\]"):
        h.service.run(boards(("alpha", "New Alpha"), ("gamma", "Gamma")))

    assert h.db.state == before, "the configuration change must be rolled back"
    assert [(tx.operation, tx.outcome) for tx in h.db.transactions] == [
        ("reconcile_config", "rolled_back")
    ]
    assert h.db.transactions[0].calls[-1] == "enabled_companies"
    assert h.db.state.runs == {}
    assert h.collector.calls == [], "no company may be fetched"
    assert h.normalizer.calls == []


# --- empty configuration and empty boards -------------------------------------


def test_empty_configuration_records_a_successful_empty_run() -> None:
    h = Harness()

    summary = h.run(boards())

    assert summary.status is CrawlRunStatus.SUCCESS
    assert summary.companies == ()
    assert summary.totals == JobCounters()
    assert h.db.operations() == ["reconcile_config", "start_run", "finish_run"]
    run = h.db.state.runs[summary.run_id]
    assert run.status is CrawlRunStatus.SUCCESS
    assert run.companies_total == 0
    assert run.counters == JobCounters()


def test_an_empty_board_succeeds_with_zero_counters() -> None:
    h = Harness({"alpha": []})

    summary = h.run(boards(("alpha", "Alpha")))

    company = by_token(summary, "alpha")
    assert company.status is CompanyCrawlStatus.SUCCESS
    assert company.counters == JobCounters()
    assert summary.status is CrawlRunStatus.SUCCESS


# --- counting ------------------------------------------------------------------


def test_first_run_counts_every_posting_as_new() -> None:
    h = Harness({"alpha": [posting(1), posting(2), posting(3)]})

    summary = h.run(boards(("alpha", "Alpha")))

    assert by_token(summary, "alpha").counters == JobCounters(fetched=3, new=3)
    assert len(h.db.state.jobs) == 3


def test_an_all_invalid_board_still_succeeds() -> None:
    # ADR-0016: normalization failures never fail a company, even all of them.
    h = Harness({"alpha": [posting(1, invalid=True), "not-an-object", {}]})

    summary = h.run(boards(("alpha", "Alpha")))

    company = by_token(summary, "alpha")
    assert company.status is CompanyCrawlStatus.SUCCESS
    assert company.counters == JobCounters(fetched=3, invalid=3)
    assert h.db.state.jobs == {}
    assert summary.status is CrawlRunStatus.SUCCESS


def test_a_mixed_board_counts_valid_and_invalid_postings() -> None:
    h = Harness({"alpha": [posting(1), posting(2, invalid=True), posting(3)]})

    summary = h.run(boards(("alpha", "Alpha")))

    assert by_token(summary, "alpha").counters == JobCounters(
        fetched=3, new=2, invalid=1
    )
    assert set(h.db.state.jobs) == {(GREENHOUSE, "1"), (GREENHOUSE, "3")}


def test_second_run_classifies_unchanged_and_updated_jobs() -> None:
    h = Harness({"alpha": [posting(1), posting(2)]})
    config = boards(("alpha", "Alpha"))
    h.run(config)

    h.collector.boards["alpha"] = [posting(1), posting(2, title="Staff Engineer")]
    h.clock.advance(timedelta(days=1))
    summary = h.run(config)

    assert by_token(summary, "alpha").counters == JobCounters(
        fetched=2, existing=2, updated=1
    )
    assert len(h.db.state.jobs) == 2


def test_company_conflict_counts_as_invalid_and_is_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Job 7 is first stored under alpha; beta later claims the same identity.
    h = Harness({"alpha": [posting(7)], "beta": [posting(7), posting(8)]})
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)

    summary = h.run(boards(("alpha", "Alpha"), ("beta", "Beta")))

    alpha = by_token(summary, "alpha")
    beta = by_token(summary, "beta")
    assert beta.status is CompanyCrawlStatus.SUCCESS
    assert beta.counters == JobCounters(fetched=2, new=1, invalid=1)
    assert h.db.state.jobs[(GREENHOUSE, "7")].company_id == alpha.company_id

    [conflict] = records(caplog, logging.ERROR)
    assert field(conflict, "operation") == "upsert_job"
    assert field(conflict, "outcome") == "company_conflict"
    assert field(conflict, "source_job_id") == "7"
    assert field(conflict, "board_token") == "beta"
    assert field(conflict, "display_name") == "Beta"
    assert field(conflict, "persisted_company_id") == alpha.company_id
    assert field(conflict, "incoming_company_id") == beta.company_id


# --- duplicate ids within one response ----------------------------------------


def test_identical_duplicate_counts_once_new_and_once_existing() -> None:
    h = Harness({"alpha": [posting(1), posting(1)]})

    summary = h.run(boards(("alpha", "Alpha")))

    assert by_token(summary, "alpha").counters == JobCounters(
        fetched=2, new=1, existing=1
    )
    assert len(h.db.state.jobs) == 1


def test_changed_duplicate_counts_as_updated_and_the_last_one_wins() -> None:
    h = Harness({"alpha": [posting(1, title="First"), posting(1, title="Second")]})

    summary = h.run(boards(("alpha", "Alpha")))

    assert by_token(summary, "alpha").counters == JobCounters(
        fetched=2, new=1, existing=1, updated=1
    )
    assert h.db.state.jobs[(GREENHOUSE, "1")].content["title"] == "Second"


def test_invalid_duplicate_counts_as_invalid_and_changes_nothing() -> None:
    h = Harness(
        {"alpha": [posting(1, title="Kept"), posting(1, invalid=True, title="Lost")]}
    )

    summary = h.run(boards(("alpha", "Alpha")))

    assert by_token(summary, "alpha").counters == JobCounters(
        fetched=2, new=1, invalid=1
    )
    assert h.db.state.jobs[(GREENHOUSE, "1")].content["title"] == "Kept"


def test_postings_are_processed_in_response_order() -> None:
    h = Harness({"alpha": [posting(3), posting(1), posting(2)]})

    h.run(boards(("alpha", "Alpha")))

    seen = [call[0] for call in h.normalizer.calls]
    assert seen == [posting(3), posting(1), posting(2)]


# --- collector failures -------------------------------------------------------


def collector_error(token: str, message: str = "HTTP 500: oops") -> CollectorError:
    return CollectorError(
        source=GREENHOUSE, company=token, operation="fetch", message=message
    )


def test_collector_failure_fails_one_company_and_the_run_continues(
    caplog: pytest.LogCaptureFixture,
) -> None:
    h = Harness({"alpha": collector_error("alpha"), "beta": [posting(1), posting(2)]})
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)

    summary = h.run(boards(("alpha", "Alpha"), ("beta", "Beta")))

    assert summary.status is CrawlRunStatus.PARTIAL_SUCCESS
    alpha = by_token(summary, "alpha")
    assert alpha.status is CompanyCrawlStatus.FAILED
    assert alpha.error_type == "CollectorError"
    assert alpha.error_message == "HTTP 500: oops"
    assert by_token(summary, "beta").counters == JobCounters(fetched=2, new=2)

    [failure] = records(caplog, logging.ERROR)
    assert field(failure, "source") == GREENHOUSE
    assert field(failure, "board_token") == "alpha"
    assert field(failure, "display_name") == "Alpha"
    assert field(failure, "operation") == "fetch"
    assert field(failure, "error_type") == "CollectorError"
    assert field(failure, "error") == "HTTP 500: oops"


def test_collector_failure_is_recorded_in_its_own_transaction() -> None:
    h = Harness({"alpha": collector_error("alpha")})

    summary = h.run(boards(("alpha", "Alpha")))

    assert h.db.operations() == [
        "reconcile_config",
        "start_run",
        "record_company_failure",
        "finish_run",
    ]
    [result] = h.db.state.results
    assert result.status is CompanyCrawlStatus.FAILED
    assert result.counters == JobCounters()
    assert result.error_type == "CollectorError"
    assert result.error_message == "HTTP 500: oops"
    assert summary.status is CrawlRunStatus.FAILED


def test_a_failed_company_leaves_its_stored_jobs_untouched() -> None:
    h = Harness({"alpha": [posting(1), posting(2)]})
    config = boards(("alpha", "Alpha"))
    h.run(config)
    before = {k: (v.content, v.last_seen_at) for k, v in h.db.state.jobs.items()}

    h.collector.boards["alpha"] = collector_error("alpha")
    h.clock.advance(timedelta(days=1))
    h.run(config)

    after = {k: (v.content, v.last_seen_at) for k, v in h.db.state.jobs.items()}
    assert after == before


def test_every_company_failing_makes_the_run_failed() -> None:
    h = Harness({"alpha": collector_error("alpha"), "beta": collector_error("beta")})

    summary = h.run(boards(("alpha", "Alpha"), ("beta", "Beta")))

    assert summary.status is CrawlRunStatus.FAILED
    assert summary.companies_failed == 2
    assert summary.companies_succeeded == 0


def test_long_collector_messages_are_bounded() -> None:
    h = Harness({"alpha": collector_error("alpha", message="x" * 5_000)})

    summary = h.run(boards(("alpha", "Alpha")))

    message = by_token(summary, "alpha").error_message
    assert message is not None
    assert len(message) == MAX_ERROR_MESSAGE_CHARS
    assert h.db.state.results[0].error_message == message


# --- persistence failures -------------------------------------------------------


def test_persistence_failure_mid_batch_rolls_back_the_whole_company() -> None:
    h = Harness({"alpha": [posting(1), posting(2), posting(3)], "beta": [posting(10)]})
    h.db.fail_on("upsert", when=lambda args: args["source_job_id"] == "2")

    summary = h.run(boards(("alpha", "Alpha"), ("beta", "Beta")))

    alpha = by_token(summary, "alpha")
    assert alpha.status is CompanyCrawlStatus.FAILED
    assert alpha.error_type == "PersistenceError"
    assert alpha.error_message == "OperationalError (SQLSTATE 08006)"
    assert (GREENHOUSE, "1") not in h.db.state.jobs, "job 1 must be rolled back"
    assert set(h.db.state.jobs) == {(GREENHOUSE, "10")}
    assert summary.status is CrawlRunStatus.PARTIAL_SUCCESS
    statuses = {(r.company_id, r.status) for r in h.db.state.results}
    assert statuses == {
        (alpha.company_id, CompanyCrawlStatus.FAILED),
        (by_token(summary, "beta").company_id, CompanyCrawlStatus.SUCCESS),
    }


def test_persistence_failure_preserves_previously_stored_jobs() -> None:
    h = Harness({"alpha": [posting(1)]})
    config = boards(("alpha", "Alpha"))
    h.run(config)
    before = h.db.state.jobs[(GREENHOUSE, "1")].last_seen_at

    h.collector.boards["alpha"] = [posting(1, title="Changed"), posting(2)]
    h.db.fail_on("upsert", when=lambda args: args["source_job_id"] == "2")
    h.clock.advance(timedelta(days=1))
    h.run(config)

    row = h.db.state.jobs[(GREENHOUSE, "1")]
    assert row.last_seen_at == before
    assert row.content["title"] == "Engineer"


def test_failure_to_commit_a_company_rolls_back_its_success_record() -> None:
    h = Harness({"alpha": [posting(1)]})
    h.db.fail_on("commit", operation="persist_company")

    summary = h.run(boards(("alpha", "Alpha")))

    alpha = by_token(summary, "alpha")
    assert alpha.status is CompanyCrawlStatus.FAILED
    assert alpha.error_message == "OperationalError (SQLSTATE 08006)"
    assert h.db.state.jobs == {}
    [result] = h.db.state.results
    assert result.status is CompanyCrawlStatus.FAILED


def test_failure_writing_the_success_record_fails_the_company() -> None:
    h = Harness({"alpha": [posting(1)]})
    h.db.fail_on(
        "record_company",
        when=lambda args: args["status"] is CompanyCrawlStatus.SUCCESS,
    )

    summary = h.run(boards(("alpha", "Alpha")))

    assert by_token(summary, "alpha").status is CompanyCrawlStatus.FAILED
    assert h.db.state.jobs == {}, "jobs and success record fall together"


def test_success_record_shares_the_company_transaction() -> None:
    h = Harness({"alpha": [posting(1), posting(2)]})

    h.run(boards(("alpha", "Alpha")))

    [company_tx] = [t for t in h.db.transactions if t.operation == "persist_company"]
    # The active jobs are counted first, before any upsert (ADR-0017, E2); with
    # none active there is nothing to deactivate, so no further query.
    assert company_tx.calls == ["count_active", "upsert", "upsert", "record_company"]
    assert company_tx.outcome == "committed"


def test_failure_record_failure_aborts_the_run(
    caplog: pytest.LogCaptureFixture,
) -> None:
    h = Harness({"alpha": collector_error("alpha"), "beta": [posting(1)]})
    h.db.fail_on("record_company", operation="record_company_failure")
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)

    with pytest.raises(PersistenceError) as caught:
        h.service.run(boards(("alpha", "Alpha"), ("beta", "Beta")))

    assert caught.value.operation == "record_company_failure"
    assert h.collector.calls == ["alpha"], "no further company is attempted"
    [run] = h.db.state.runs.values()
    assert run.status is CrawlRunStatus.RUNNING, "the run is left running"
    assert h.db.state.results == []
    [critical] = records(caplog, logging.CRITICAL)
    assert field(critical, "board_token") == "alpha"
    assert field(critical, "operation") == "record_company_failure"


def test_reconciliation_failure_propagates_before_any_run_exists() -> None:
    h = Harness({"alpha": [posting(1)]})
    h.db.fail_on("sync")

    with pytest.raises(PersistenceError):
        h.service.run(boards(("alpha", "Alpha")))

    assert h.db.state.runs == {}
    assert h.collector.calls == []


def test_start_failure_propagates_before_any_company_is_crawled() -> None:
    h = Harness({"alpha": [posting(1)]})
    h.db.fail_on("start")

    with pytest.raises(PersistenceError):
        h.service.run(boards(("alpha", "Alpha")))

    assert h.collector.calls == []


def test_finish_failure_propagates_and_leaves_the_run_running() -> None:
    h = Harness({"alpha": [posting(1)]})
    h.db.fail_on("finish")

    with pytest.raises(PersistenceError):
        h.service.run(boards(("alpha", "Alpha")))

    [run] = h.db.state.runs.values()
    assert run.status is CrawlRunStatus.RUNNING
    assert len(h.db.state.jobs) == 1, "the company's own commit stands"


# --- programming errors -------------------------------------------------------


def test_unexpected_normalizer_error_propagates_and_rolls_back() -> None:
    h = Harness({"alpha": [posting(1), posting(2, bug=True)], "beta": [posting(3)]})

    with pytest.raises(RuntimeError, match="simulated programming error"):
        h.service.run(boards(("alpha", "Alpha"), ("beta", "Beta")))

    assert h.db.state.jobs == {}
    assert h.db.state.results == [], "a bug is never recorded as a company failure"
    assert h.collector.calls == ["alpha"]
    [run] = h.db.state.runs.values()
    assert run.status is CrawlRunStatus.RUNNING


@pytest.mark.parametrize(
    "error",
    [RuntimeError("bug"), ValueError("bad argument"), KeyError("missing")],
    ids=["runtime", "value", "key"],
)
def test_unexpected_collector_error_propagates(error: Exception) -> None:
    h = Harness({"alpha": error})

    with pytest.raises(type(error)):
        h.service.run(boards(("alpha", "Alpha")))

    assert h.db.state.results == []


# --- time -----------------------------------------------------------------------


def test_one_observed_at_for_every_job_in_the_run() -> None:
    clock = SteppingClock(T0)
    h = Harness({"alpha": [posting(1), posting(2)], "beta": [posting(3)]}, clock=clock)

    summary = h.run(boards(("alpha", "Alpha"), ("beta", "Beta")))

    observed = {call[2] for call in h.normalizer.calls}
    assert observed == {summary.observed_at}
    assert {row.first_seen_at for row in h.db.state.jobs.values()} == observed
    assert {row.last_seen_at for row in h.db.state.jobs.values()} == observed
    assert h.db.state.runs[summary.run_id].started_at == summary.observed_at


def test_company_timing_comes_from_the_injected_clock() -> None:
    clock = SteppingClock(T0)
    h = Harness({"alpha": [posting(1)], "beta": collector_error("beta")}, clock=clock)

    summary = h.run(boards(("alpha", "Alpha"), ("beta", "Beta")))

    alpha = by_token(summary, "alpha")
    beta = by_token(summary, "beta")
    assert summary.observed_at < alpha.started_at < alpha.finished_at
    assert alpha.finished_at < beta.started_at < beta.finished_at
    assert beta.finished_at < summary.finished_at
    recorded = {(r.company_id, r.started_at, r.finished_at) for r in h.db.state.results}
    assert recorded == {
        (alpha.company_id, alpha.started_at, alpha.finished_at),
        (beta.company_id, beta.started_at, beta.finished_at),
    }
    assert h.db.state.runs[summary.run_id].finished_at == summary.finished_at


# --- configuration reconciliation ---------------------------------------------


def test_reconciliation_runs_before_the_crawl_run_is_opened() -> None:
    h = Harness({"alpha": [posting(1)]})

    h.run(boards(("alpha", "Alpha")))

    assert h.db.operations()[:2] == ["reconcile_config", "start_run"]
    [reconcile] = [t for t in h.db.transactions if t.operation == "reconcile_config"]
    assert reconcile.calls == ["sync", "disable_absent", "enabled_companies"]


def test_disabled_and_removed_boards_are_not_crawled() -> None:
    h = Harness({"alpha": [posting(1)], "gone": [posting(2)], "off": [posting(3)]})
    gone = h.db.seed_company("gone", "Gone")

    summary = h.run(boards(("alpha", "Alpha"), ("off", "Off", False)))

    assert h.collector.calls == ["alpha"]
    assert [c.board_token for c in summary.companies] == ["alpha"]
    assert h.db.state.companies[(GREENHOUSE, "off")].enabled is False
    assert h.db.state.companies[(GREENHOUSE, "gone")].enabled is False
    assert h.db.state.companies[(GREENHOUSE, "gone")].id == gone.id, "never deleted"
    assert {r.company_id for r in h.db.state.results} == {
        by_token(summary, "alpha").company_id
    }


def test_removing_a_board_does_not_touch_its_jobs() -> None:
    h = Harness({"alpha": [posting(1)], "beta": [posting(2)]})
    h.run(boards(("alpha", "Alpha"), ("beta", "Beta")))
    before = h.db.state.jobs[(GREENHOUSE, "2")]

    h.clock.advance(timedelta(days=1))
    h.run(boards(("alpha", "Alpha")))

    assert h.db.state.jobs[(GREENHOUSE, "2")] == before


def test_companies_are_crawled_in_reconciled_order_with_configured_identity() -> None:
    h = Harness({"zeta": [posting(1)], "alpha": [posting(2)]})

    summary = h.run(boards(("zeta", "Zeta Corp"), ("alpha", "Alpha Inc")))

    assert h.collector.calls == ["alpha", "zeta"]
    assert [call[1] for call in h.normalizer.calls] == ["Alpha Inc", "Zeta Corp"]
    assert [c.display_name for c in summary.companies] == ["Alpha Inc", "Zeta Corp"]


def test_display_name_changes_are_reconciled_before_crawling() -> None:
    h = Harness({"alpha": [posting(1)]})
    h.run(boards(("alpha", "Old Name")))

    h.run(boards(("alpha", "New Name")))

    assert h.normalizer.calls[-1][1] == "New Name"
    assert h.db.state.companies[(GREENHOUSE, "alpha")].display_name == "New Name"


# --- transaction boundaries ---------------------------------------------------


def test_fetching_happens_outside_any_transaction() -> None:
    h = Harness({"alpha": [posting(1)], "beta": collector_error("beta")})

    h.run(boards(("alpha", "Alpha"), ("beta", "Beta")))

    assert h.collector.open_transactions_during_fetch == [0, 0]


def test_every_transaction_is_closed_and_resolved() -> None:
    h = Harness(
        {"alpha": [posting(1)], "beta": collector_error("beta"), "gamma": [posting(2)]}
    )
    h.db.fail_on("upsert", when=lambda args: args["source_job_id"] == "2")

    h.run(boards(("alpha", "Alpha"), ("beta", "Beta"), ("gamma", "Gamma")))

    assert h.db.open_transactions == 0
    assert [(t.operation, t.outcome) for t in h.db.transactions] == [
        ("reconcile_config", "committed"),
        ("start_run", "committed"),
        ("persist_company", "committed"),
        ("record_company_failure", "committed"),
        ("persist_company", "rolled_back"),
        ("record_company_failure", "committed"),
        ("finish_run", "committed"),
    ]


def test_run_row_matches_the_returned_summary() -> None:
    h = Harness(
        {"alpha": [posting(1), posting(2, invalid=True)], "beta": collector_error("b")}
    )

    summary = h.run(boards(("alpha", "Alpha"), ("beta", "Beta")))

    run = h.db.state.runs[summary.run_id]
    assert run.status is summary.status is CrawlRunStatus.PARTIAL_SUCCESS
    assert run.companies_total == 2
    assert run.companies_succeeded == summary.companies_succeeded == 1
    assert run.companies_failed == summary.companies_failed == 1
    assert run.counters == summary.totals == JobCounters(fetched=2, new=1, invalid=1)


# --- structured logging -------------------------------------------------------


def test_rejected_posting_log_carries_company_context(
    caplog: pytest.LogCaptureFixture,
) -> None:
    h = Harness({"alpha": [posting(5, invalid=True)]})
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)

    h.run(boards(("alpha", "Alpha")))

    [warning] = records(caplog, logging.WARNING)
    assert field(warning, "source") == GREENHOUSE
    assert field(warning, "board_token") == "alpha"
    assert field(warning, "display_name") == "Alpha"
    assert field(warning, "operation") == "normalize"
    assert field(warning, "source_job_id") == "5"
    assert field(warning, "error_type") == "NormalizationError"
    assert field(warning, "error") == "missing title"


def test_persistence_failure_log_carries_safe_context(
    caplog: pytest.LogCaptureFixture,
) -> None:
    h = Harness({"alpha": [posting(1), posting(2)]})
    h.db.fail_on("upsert", when=lambda args: args["source_job_id"] == "2")
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)

    h.run(boards(("alpha", "Alpha")))

    [failure] = records(caplog, logging.ERROR)
    assert field(failure, "operation") == "persist_company"
    assert field(failure, "error_type") == "PersistenceError"
    assert field(failure, "error") == "OperationalError (SQLSTATE 08006)"
    assert field(failure, "fetched") == 2, "what was fetched lives only in the log"
    assert failure.exc_info is None, "no traceback is attached"


def test_completion_record_carries_status_and_totals(
    caplog: pytest.LogCaptureFixture,
) -> None:
    h = Harness({"alpha": [posting(1), posting(2, invalid=True)]})
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)

    summary = h.run(boards(("alpha", "Alpha")))

    [completion] = records(caplog, logging.INFO)
    assert field(completion, "operation") == "crawl_run"
    assert field(completion, "run_id") == summary.run_id
    assert field(completion, "status") == "success"
    assert field(completion, "companies_total") == 1
    assert (
        field(completion, "fetched"),
        field(completion, "new"),
        field(completion, "existing"),
        field(completion, "updated"),
        field(completion, "invalid"),
    ) == (2, 1, 0, 0, 1)


def test_no_deactivation_happens() -> None:
    # ADR-0004: nothing in the MVP sets is_active = false.
    h = Harness({"alpha": [posting(1), posting(2)]})
    config = boards(("alpha", "Alpha"))
    h.run(config)

    h.collector.boards["alpha"] = []
    h.run(config)

    assert set(h.db.state.jobs) == {(GREENHOUSE, "1"), (GREENHOUSE, "2")}
    assert all(row.content["is_active"] for row in h.db.state.jobs.values())
