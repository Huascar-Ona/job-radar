"""Committed pipeline runs are durable and visible to another connection.

The only pipeline tests that really commit. A SAVEPOINT-based test cannot show
that the store adapter's commits reach the database, so these run the service
with ordinary sessions and read the result back over a separate, unpooled
connection.

Isolation: unique board tokens, job ids and run instants that nothing else
uses. Before writing, the fixture checks the database identity and that none
of those identifiers exist, and that no company is enabled: a run disables
every enabled company missing from its configuration, so an enabled one would
be modified. Any of these failing stops the test before a single write.
Cleanup deletes only rows carrying those identifiers, and runs even when an
assertion fails.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

from job_radar.ingestion.summary import CrawlRunSummary
from job_radar.persistence.session import create_session_factory
from tests.support.clock import FixedClock
from tests.support.ingestion_fakes import boards
from tests.support.pipeline import (
    SCHEMA_DB,
    FakeGreenhouse,
    assert_run_is_consistent,
    assert_schema_test_database,
    board,
    counters,
    jobs_of,
    posting,
    results_of,
    run_pipeline,
    run_row,
)

pytestmark = pytest.mark.integration

TOKEN = "durable-pipeline-alpha"
JOB_IDS = (990_000_000_001, 990_000_000_002, 990_000_000_003)
#: Distinctive instants: a run's started_at is its observed_at, so these
#: identify this test's runs and nothing else's.
FIRST = datetime(2031, 1, 1, 0, 0, 7, tzinfo=UTC)
SECOND = FIRST + timedelta(hours=1)


@dataclass
class Durable:
    engine: Engine
    verifier: Engine
    greenhouse: FakeGreenhouse
    clock: FixedClock

    def run(self) -> CrawlRunSummary:
        return run_pipeline(
            sessions=create_session_factory(self.engine),
            greenhouse=self.greenhouse,
            config=boards((TOKEN, "Durable Alpha")),
            clock=self.clock,
        )


def _check_untouched(connection: Connection) -> None:
    assert_schema_test_database(connection)
    enabled = connection.execute(
        text("SELECT count(*) FROM companies WHERE is_enabled")
    ).scalar_one()
    ours = connection.execute(
        text(
            "SELECT"
            " (SELECT count(*) FROM companies WHERE board_token = :token)"
            " + (SELECT count(*) FROM jobs WHERE source = 'greenhouse'"
            "    AND source_job_id = ANY(:ids))"
            " + (SELECT count(*) FROM crawl_runs WHERE started_at = ANY(:instants))"
        ),
        {
            "token": TOKEN,
            "ids": [str(job_id) for job_id in JOB_IDS],
            "instants": [FIRST, SECOND],
        },
    ).scalar_one()
    if enabled or ours:
        pytest.fail(
            "refusing to commit: the schema test database already holds"
            f" {enabled} enabled companies and {ours} rows with this test's"
            " identifiers; a run would modify or collide with them"
        )


def _clean_up(connection: Connection) -> None:
    assert_schema_test_database(connection)
    company_ids = (
        connection.execute(
            text(
                "SELECT id FROM companies"
                " WHERE source = 'greenhouse' AND board_token = :token"
            ),
            {"token": TOKEN},
        )
        .scalars()
        .all()
    )
    # Children first: results and jobs reference companies with RESTRICT.
    connection.execute(
        text("DELETE FROM crawl_runs WHERE started_at = ANY(:instants)"),
        {"instants": [FIRST, SECOND]},
    )
    if not company_ids:
        return
    ids = {"ids": list(company_ids)}
    connection.execute(
        text("DELETE FROM crawl_company_results WHERE company_id = ANY(:ids)"), ids
    )
    connection.execute(text("DELETE FROM jobs WHERE company_id = ANY(:ids)"), ids)
    connection.execute(text("DELETE FROM companies WHERE id = ANY(:ids)"), ids)


@pytest.fixture
def durable(schema_database_url: str) -> Iterator[Durable]:
    if make_url(schema_database_url).database != SCHEMA_DB:
        pytest.fail(f"refusing to commit outside {SCHEMA_DB}")
    engine = create_engine(schema_database_url)
    # Unpooled: every check opens a brand-new connection, which can only see
    # what was really committed.
    verifier = create_engine(schema_database_url, poolclass=NullPool)
    try:
        with verifier.connect() as connection:
            _check_untouched(connection)
        try:
            greenhouse = FakeGreenhouse()
            greenhouse.serve(TOKEN, board(*(posting(job_id) for job_id in JOB_IDS)))
            yield Durable(engine, verifier, greenhouse, FixedClock(FIRST))
        finally:
            with verifier.begin() as connection:
                _clean_up(connection)
    finally:
        engine.dispose()
        verifier.dispose()


def test_a_committed_run_is_visible_to_a_new_connection(durable: Durable) -> None:
    summary = durable.run()

    with durable.verifier.connect() as fresh:
        rows = jobs_of(fresh, TOKEN)
        assert set(rows) == {str(job_id) for job_id in JOB_IDS}
        assert all(row["first_seen_at"] == FIRST for row in rows.values())
        run = run_row(fresh, summary.run_id)
        assert run["status"] == "success"
        assert run["started_at"] == FIRST
        assert counters(run) == (3, 3, 0, 0, 0)
        assert results_of(fresh, summary.run_id)[TOKEN]["status"] == "success"
        assert_run_is_consistent(fresh, summary.run_id)


def test_a_committed_rerun_stays_idempotent(durable: Durable) -> None:
    durable.run()
    durable.clock.set(SECOND)

    summary = durable.run()

    with durable.verifier.connect() as fresh:
        rows = jobs_of(fresh, TOKEN)
        assert len(rows) == len(JOB_IDS)
        assert all(row["first_seen_at"] == FIRST for row in rows.values())
        assert all(row["last_seen_at"] == SECOND for row in rows.values())
        result = results_of(fresh, summary.run_id)[TOKEN]
        assert counters(result) == (3, 0, 3, 0, 0)
        assert_run_is_consistent(fresh, summary.run_id)
