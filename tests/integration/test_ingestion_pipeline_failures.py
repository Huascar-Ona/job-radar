"""Persistence failures in the real pipeline, raised by PostgreSQL itself.

A temporary trigger makes PostgreSQL raise ``P0001`` on chosen writes, so the
error travels the real path: psycopg, SQLAlchemy, the store adapter, the
service. Triggers are created on the rollback-protected test connection
(``pipeline_connection``) and disappear with its transaction.

Expected category: psycopg maps ``P0001`` (raise_exception) to
``psycopg.errors.RaiseException``, a ``ProgrammingError`` subclass, which
SQLAlchemy wraps as ``ProgrammingError``.
"""

import logging
import traceback
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import Connection, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from job_radar.config.boards import BoardsConfig
from job_radar.domain.errors import PersistenceError
from job_radar.domain.job import JobSource, NormalizedJob
from job_radar.ingestion.summary import CrawlRunSummary
from job_radar.persistence.job_repository import JobRepository
from tests.support.clock import FixedClock
from tests.support.ingestion_fakes import boards
from tests.support.pipeline import (
    FakeGreenhouse,
    assert_run_is_consistent,
    board,
    company_row,
    counters,
    install_failing_trigger,
    jobs_of,
    last_run_id,
    posting,
    results_of,
    run_pipeline,
    run_row,
    runs_after,
    savepoint_sessions,
)

pytestmark = pytest.mark.integration

T0 = datetime(2026, 11, 11, 9, 0, tzinfo=UTC)
SECRET = "SECRET-MARKER-41c9"
INJECTED = "ProgrammingError (SQLSTATE P0001)"


class Pipeline:
    def __init__(self, connection: Connection) -> None:
        self.connection = connection
        self.greenhouse = FakeGreenhouse()
        self.clock = FixedClock(T0)

    def run(self, config: BoardsConfig) -> CrawlRunSummary:
        return run_pipeline(
            sessions=savepoint_sessions(self.connection),
            greenhouse=self.greenhouse,
            config=config,
            clock=self.clock,
        )

    def later(self) -> None:
        self.clock.advance(timedelta(hours=1))
        self.greenhouse.requests.clear()


@pytest.fixture
def pipeline(pipeline_connection: Connection) -> Pipeline:
    return Pipeline(pipeline_connection)


def fail_job(connection: Connection, source_job_id: str, message: str) -> None:
    install_failing_trigger(
        connection,
        name="test_fail_job",
        table="jobs",
        event="INSERT OR UPDATE",
        condition=f"NEW.source_job_id = '{source_job_id}'",
        message=message,
    )


def assert_safe(error: BaseException) -> None:
    assert error.__cause__ is None
    assert error.__context__ is None
    assert SECRET not in "".join(traceback.format_exception(error))


def log_text(caplog: pytest.LogCaptureFixture) -> str:
    parts = [caplog.text]
    for record in caplog.records:
        parts.append(record.getMessage())
        parts.extend(repr(value) for value in record.__dict__.values())
    return "\n".join(parts)


# --- a company's transaction is all or nothing ----------------------------------


def test_a_failure_mid_batch_rolls_back_the_whole_company(pipeline: Pipeline) -> None:
    pipeline.greenhouse.serve("alpha", board(posting(801), posting(802)))
    pipeline.run(boards(("alpha", "Alpha")))
    before = jobs_of(pipeline.connection, "alpha")
    pipeline.later()
    fail_job(pipeline.connection, "899", "'injected failure'")
    pipeline.greenhouse.serve(
        "alpha",
        board(
            posting(801, title="Changed"),  # an update, then
            posting(802, title="Changed"),  # another,
            posting(803),  # an insert,
            posting(899),  # then PostgreSQL refuses this one
            posting(804),
        ),
    )

    summary = pipeline.run(boards(("alpha", "Alpha")))

    # Every write of this run was rolled back to the company's SAVEPOINT: the
    # earlier rows are visible again unchanged, row versions included, and
    # neither insert survives.
    assert jobs_of(pipeline.connection, "alpha") == before
    result = results_of(pipeline.connection, summary.run_id)["alpha"]
    assert result["status"] == "failed"
    assert counters(result) == (0, 0, 0, 0, 0)
    assert result["error_type"] == "PersistenceError"
    assert result["error_message"] == INJECTED
    assert run_row(pipeline.connection, summary.run_id)["status"] == "failed"
    assert_run_is_consistent(pipeline.connection, summary.run_id)


def test_companies_before_and_after_a_failed_one_are_kept(pipeline: Pipeline) -> None:
    fail_job(pipeline.connection, "911", "'injected failure'")
    pipeline.greenhouse.serve("alpha", board(posting(901)))
    pipeline.greenhouse.serve("beta", board(posting(910), posting(911)))
    pipeline.greenhouse.serve("gamma", board(posting(921)))

    summary = pipeline.run(boards(("alpha", "A"), ("beta", "B"), ("gamma", "C")))

    conn = pipeline.connection
    assert set(jobs_of(conn, "alpha")) == {"901"}
    assert jobs_of(conn, "beta") == {}
    assert set(jobs_of(conn, "gamma")) == {"921"}
    results = results_of(conn, summary.run_id)
    assert {token: row["status"] for token, row in results.items()} == {
        "alpha": "success",
        "beta": "failed",
        "gamma": "success",
    }
    assert run_row(conn, summary.run_id)["status"] == "partial_success"
    assert_run_is_consistent(conn, summary.run_id)


# --- nothing from the database or the source reaches persisted errors or logs -----


def test_driver_and_source_text_never_reach_results_or_logs(
    pipeline: Pipeline, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    # The trigger's message quotes the marker and the row's raw_data, which
    # itself carries the marker: exactly what a real driver message can hold.
    fail_job(pipeline.connection, "951", f"'{SECRET} ' || NEW.raw_data::text")
    pipeline.greenhouse.serve("alpha", board(posting(951, content=SECRET)))
    pipeline.greenhouse.serve("beta", httpx.Response(503, text=f"<p>{SECRET}</p>"))

    summary = pipeline.run(boards(("alpha", "Alpha"), ("beta", "Beta")))

    conn = pipeline.connection
    results = results_of(conn, summary.run_id)
    assert results["alpha"]["error_message"] == INJECTED
    assert results["beta"]["error_message"] == "HTTP 503"
    leaked = conn.execute(
        text(
            "SELECT count(*) FROM crawl_company_results"
            " WHERE crawl_run_id = :id AND (coalesce(error_type, '') LIKE :marker"
            " OR coalesce(error_message, '') LIKE :marker)"
        ),
        {"id": summary.run_id, "marker": f"%{SECRET}%"},
    ).scalar_one()
    assert leaked == 0
    assert SECRET not in log_text(caplog)
    assert SECRET not in repr(summary)


def test_the_injected_driver_error_really_carries_the_marker(
    pipeline: Pipeline,
) -> None:
    # Guards the test above: if PostgreSQL's message lacked the marker, its
    # "never reaches" assertions would pass without proving anything.
    conn = pipeline.connection
    fail_job(conn, "951", f"'{SECRET} ' || NEW.raw_data::text")
    company_id = conn.execute(
        text(
            "INSERT INTO companies (source, board_token, display_name)"
            " VALUES ('greenhouse', 'alpha', 'Alpha') RETURNING id"
        )
    ).scalar_one()
    job = NormalizedJob(
        source=JobSource.GREENHOUSE,
        source_job_id="951",
        company="Alpha",
        title="Engineer",
        job_url="https://jobs.example.test/951",
        observed_at=T0,
        raw_data={"content": SECRET},
    )
    session = Session(bind=conn, join_transaction_mode="create_savepoint")
    try:
        with pytest.raises(DBAPIError) as caught:
            JobRepository(session).upsert(job, company_id=int(company_id))
    finally:
        session.rollback()
        session.close()

    assert SECRET in str(caught.value)
    assert getattr(caught.value.orig, "sqlstate", None) == "P0001"


# --- failures outside a company's processing ----------------------------------------


def test_a_failure_record_that_cannot_be_written_aborts_the_run(
    pipeline: Pipeline, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    conn = pipeline.connection
    install_failing_trigger(
        conn,
        name="test_fail_failure_record",
        table="crawl_company_results",
        event="INSERT",
        condition="NEW.status = 'failed'",
        message=f"'{SECRET}'",
    )
    pipeline.greenhouse.serve("alpha", httpx.Response(503))
    pipeline.greenhouse.serve("beta", board(posting(961)))
    runs_before = last_run_id(conn)

    with pytest.raises(PersistenceError) as caught:
        pipeline.run(boards(("alpha", "Alpha"), ("beta", "Beta")))

    assert caught.value.operation == "record_company_failure"
    assert caught.value.message == INJECTED
    assert_safe(caught.value)
    assert SECRET not in log_text(caplog)
    [run] = runs_after(conn, runs_before)
    assert run["status"] == "running"
    assert run["finished_at"] is None
    assert counters(run) == (0, 0, 0, 0, 0)
    assert results_of(conn, int(str(run["id"]))) == {}
    assert pipeline.greenhouse.requests == ["alpha"], "the run stops at alpha"
    assert jobs_of(conn, "beta") == {}


def test_a_run_that_cannot_be_finished_stays_running(pipeline: Pipeline) -> None:
    conn = pipeline.connection
    install_failing_trigger(
        conn,
        name="test_fail_finish",
        table="crawl_runs",
        event="UPDATE",
        condition="true",
    )
    pipeline.greenhouse.serve("alpha", board(posting(971)))
    runs_before = last_run_id(conn)

    with pytest.raises(PersistenceError) as caught:
        pipeline.run(boards(("alpha", "Alpha")))

    assert caught.value.operation == "finish_run"
    assert_safe(caught.value)
    [run] = runs_after(conn, runs_before)
    assert run["status"] == "running"
    assert run["finished_at"] is None
    # The company's own transaction had committed before finishing failed.
    result = results_of(conn, int(str(run["id"])))["alpha"]
    assert result["status"] == "success"
    assert counters(result) == (1, 1, 0, 0, 0)
    assert set(jobs_of(conn, "alpha")) == {"971"}


def test_a_run_that_cannot_be_opened_keeps_reconciliation(pipeline: Pipeline) -> None:
    conn = pipeline.connection
    install_failing_trigger(
        conn,
        name="test_fail_start",
        table="crawl_runs",
        event="INSERT",
        condition="true",
    )
    runs_before = last_run_id(conn)

    with pytest.raises(PersistenceError) as caught:
        pipeline.run(boards(("alpha", "Alpha")))

    assert caught.value.operation == "start_run"
    assert_safe(caught.value)
    assert runs_after(conn, runs_before) == []
    # Reconciliation is its own transaction and had committed (SPEC,
    # "Ingestion run").
    assert company_row(conn, "alpha")["is_enabled"] is True
    assert pipeline.greenhouse.requests == []
    assert jobs_of(conn, "alpha") == {}
