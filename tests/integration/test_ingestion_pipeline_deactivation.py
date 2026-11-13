"""Deactivation on verified absence, end to end against PostgreSQL (ADR-0017).

Real IngestionService, GreenhouseCollector, GreenhouseNormalizer, store
adapter, repositories and database; only HTTP is mocked. Each test runs
inside one transaction that is rolled back (``pipeline_connection``); the
service's transactions are SAVEPOINTs within it, and the failure trigger is
created on that connection and disappears with it.
"""

import logging
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import Connection

from job_radar.config.boards import BoardsConfig
from job_radar.ingestion.service import LOGGER_NAME
from job_radar.ingestion.summary import CrawlRunSummary
from tests.support.clock import FixedClock
from tests.support.ingestion_fakes import boards
from tests.support.pipeline import (
    FakeGreenhouse,
    Row,
    assert_run_is_consistent,
    board,
    counters,
    install_failing_trigger,
    jobs_of,
    posting,
    results_of,
    run_pipeline,
    run_row,
    savepoint_sessions,
)

pytestmark = pytest.mark.integration

T0 = datetime(2026, 11, 12, 9, 0, tzinfo=UTC)
T1 = T0 + timedelta(hours=1)
T2 = T1 + timedelta(hours=1)
ALPHA = boards(("alpha", "Alpha"))


class Pipeline:
    def __init__(self, connection: Connection) -> None:
        self.connection = connection
        self.greenhouse = FakeGreenhouse()
        self.clock = FixedClock(T0)

    def run(self, config: BoardsConfig = ALPHA) -> CrawlRunSummary:
        summary = run_pipeline(
            sessions=savepoint_sessions(self.connection),
            greenhouse=self.greenhouse,
            config=config,
            clock=self.clock,
        )
        assert_run_is_consistent(self.connection, summary.run_id)
        return summary

    def seed(self, *ids: int) -> None:
        """A first run storing ``ids``; then time moves on."""
        self.greenhouse.serve("alpha", board(*(posting(i) for i in ids)))
        self.run()
        self.clock.advance(timedelta(hours=1))

    def jobs(self) -> dict[str, Row]:
        return jobs_of(self.connection, "alpha")

    def active(self) -> list[int]:
        return sorted(int(k) for k, row in self.jobs().items() if row["is_active"])

    def result(self, summary: CrawlRunSummary) -> Row:
        return results_of(self.connection, summary.run_id)["alpha"]


@pytest.fixture
def pipeline(pipeline_connection: Connection) -> Pipeline:
    return Pipeline(pipeline_connection)


def skip_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [
        r
        for r in caplog.records
        if r.name == LOGGER_NAME and hasattr(r, "deactivation_skipped")
    ]


# --- deactivation and reactivation -------------------------------------------------


def test_a_job_closed_on_a_verified_board_is_deactivated_not_deleted(
    pipeline: Pipeline,
) -> None:
    pipeline.seed(101, 102, 103)
    before = pipeline.jobs()["103"]
    pipeline.greenhouse.serve("alpha", board(posting(101), posting(102)))

    summary = pipeline.run()

    closed = pipeline.jobs()["103"]
    assert closed["is_active"] is False
    assert closed["deactivated_at"] == T1
    for unchanged in ("first_seen_at", "last_seen_at", "raw_data", "content_hash"):
        assert closed[unchanged] == before[unchanged], unchanged
    assert pipeline.active() == [101, 102]
    result = pipeline.result(summary)
    assert result["status"] == "success"
    assert result["jobs_deactivated"] == 1
    assert result["completeness_verified"] is True
    assert counters(result) == (2, 0, 2, 0, 0)
    assert run_row(pipeline.connection, summary.run_id)["jobs_deactivated"] == 1
    assert summary.totals.deactivated == 1


def test_a_deactivated_job_that_reappears_is_active_again(pipeline: Pipeline) -> None:
    pipeline.seed(101, 102, 103)
    pipeline.greenhouse.serve("alpha", board(posting(101), posting(102)))
    pipeline.run()
    pipeline.clock.advance(timedelta(hours=1))
    pipeline.greenhouse.serve("alpha", board(posting(101), posting(102), posting(103)))

    summary = pipeline.run()

    back = pipeline.jobs()["103"]
    assert back["is_active"] is True
    assert back["deactivated_at"] is None
    assert back["last_seen_at"] == T2
    assert counters(pipeline.result(summary)) == (3, 0, 3, 0, 0)
    assert pipeline.result(summary)["jobs_deactivated"] == 0


def test_an_unchanged_rerun_deactivates_nothing(pipeline: Pipeline) -> None:
    pipeline.seed(101, 102)
    pipeline.greenhouse.serve("alpha", board(posting(101), posting(102)))

    summary = pipeline.run()

    assert pipeline.active() == [101, 102]
    assert pipeline.result(summary)["jobs_deactivated"] == 0


# --- guards ------------------------------------------------------------------------


def test_a_failed_crawl_changes_no_job(pipeline: Pipeline) -> None:
    pipeline.seed(101, 102)
    before = pipeline.jobs()
    pipeline.greenhouse.serve("alpha", httpx.Response(500))

    summary = pipeline.run()

    assert pipeline.jobs() == before, "byte for byte, xmin included"
    result = pipeline.result(summary)
    assert result["status"] == "failed"
    assert result["completeness_verified"] is None


def test_unverified_completeness_deactivates_nothing(
    pipeline: Pipeline, caplog: pytest.LogCaptureFixture
) -> None:
    pipeline.seed(101, 102, 103)
    pipeline.greenhouse.serve("alpha", {"jobs": [posting(101)]})  # no meta.total
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)

    summary = pipeline.run()

    assert pipeline.active() == [101, 102, 103]
    result = pipeline.result(summary)
    assert result["status"] == "success"
    assert result["completeness_verified"] is False
    assert result["jobs_deactivated"] == 0
    [record] = skip_records(caplog)
    assert record.deactivation_skipped == "completeness_unverified"  # type: ignore[attr-defined]


def test_an_empty_board_deactivates_nothing(pipeline: Pipeline) -> None:
    pipeline.seed(101, 102)
    pipeline.greenhouse.serve("alpha", board())

    summary = pipeline.run()

    assert pipeline.active() == [101, 102]
    assert pipeline.result(summary)["jobs_deactivated"] == 0


def test_an_invalid_posting_with_a_known_id_protects_its_job(
    pipeline: Pipeline,
) -> None:
    pipeline.seed(101, 102, 103)
    pipeline.greenhouse.serve("alpha", board(posting(101), posting(103, title=None)))

    summary = pipeline.run()

    assert pipeline.active() == [101, 103]
    assert pipeline.result(summary)["jobs_deactivated"] == 1


def test_an_invalid_posting_without_an_id_deactivates_nothing(
    pipeline: Pipeline, caplog: pytest.LogCaptureFixture
) -> None:
    pipeline.seed(101, 102, 103)
    pipeline.greenhouse.serve("alpha", board(posting(101), posting("not-an-id")))
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)

    pipeline.run()

    assert pipeline.active() == [101, 102, 103]
    [record] = skip_records(caplog)
    assert record.deactivation_skipped == "invalid_posting_without_id"  # type: ignore[attr-defined]


def test_a_board_of_only_invalid_postings_deactivates_nothing(
    pipeline: Pipeline,
) -> None:
    pipeline.seed(101, 102)
    pipeline.greenhouse.serve("alpha", board(posting(101, title=None)))

    pipeline.run()

    assert pipeline.active() == [101, 102]


def test_a_disabled_company_is_not_crawled_and_keeps_its_jobs(
    pipeline: Pipeline,
) -> None:
    pipeline.seed(101, 102)
    before = pipeline.jobs()

    pipeline.run(boards(("alpha", "Alpha", False)))

    assert pipeline.jobs() == before


# --- large disappearance (D6') -------------------------------------------------------


def test_a_large_disappearance_is_skipped_with_a_warning(
    pipeline: Pipeline, caplog: pytest.LogCaptureFixture
) -> None:
    pipeline.seed(*range(201, 211))  # P = 10
    pipeline.greenhouse.serve("alpha", board(*(posting(i) for i in range(201, 205))))
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)

    summary = pipeline.run()  # K = 6

    assert pipeline.active() == list(range(201, 211))
    result = pipeline.result(summary)
    assert result["status"] == "success"
    assert result["jobs_deactivated"] == 0
    assert counters(result) == (4, 0, 4, 0, 0), "the upserts stand"
    assert run_row(pipeline.connection, summary.run_id)["status"] == "success"
    [record] = skip_records(caplog)
    assert record.levelno == logging.WARNING
    assert record.deactivation_skipped == "large_disappearance"  # type: ignore[attr-defined]
    assert (record.absent, record.previously_active) == (6, 10)  # type: ignore[attr-defined]


def test_exactly_half_is_deactivated(pipeline: Pipeline) -> None:
    pipeline.seed(*range(201, 211))
    pipeline.greenhouse.serve("alpha", board(*(posting(i) for i in range(201, 206))))

    summary = pipeline.run()  # K = 5 of 10

    assert pipeline.active() == list(range(201, 206))
    assert pipeline.result(summary)["jobs_deactivated"] == 5


def test_fewer_than_five_absent_is_deactivated_even_if_all(pipeline: Pipeline) -> None:
    pipeline.seed(201, 202, 203, 204)
    pipeline.greenhouse.serve("alpha", board(posting(299)))

    summary = pipeline.run()  # P = 4, K = 4

    assert pipeline.active() == [299]
    assert pipeline.result(summary)["jobs_deactivated"] == 4


# --- transactions ------------------------------------------------------------------


def test_a_failing_deactivation_rolls_back_the_whole_company(
    pipeline: Pipeline,
) -> None:
    pipeline.seed(101, 102, 103)
    before = pipeline.jobs()
    install_failing_trigger(
        pipeline.connection,
        name="test_fail_deactivation",
        table="jobs",
        event="UPDATE",
        condition="OLD.is_active AND NOT NEW.is_active",
    )
    pipeline.greenhouse.serve("alpha", board(posting(101), posting(102)))

    summary = pipeline.run()

    # The upserts of 101 and 102 rolled back with the failed deactivation.
    assert pipeline.jobs() == before, "byte for byte, xmin included"
    result = pipeline.result(summary)
    assert result["status"] == "failed"
    assert result["error_type"] == "PersistenceError"
    assert result["jobs_deactivated"] == 0
