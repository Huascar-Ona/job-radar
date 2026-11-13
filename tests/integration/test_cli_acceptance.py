"""Acceptance: the ingest command end to end (ADR-0010, layer 4).

SPEC, "MVP Acceptance Criteria", driven through the CLI's own composition
root, ``run_ingest``: real settings, board file, engine, HTTP client,
collector, normalizer, ingestion service, store adapter and PostgreSQL. Only
the HTTP transport is stubbed and the clock fixed, so responses and
timestamps are deterministic.

The CLI owns its engine and commits for real, so these tests follow the
durability tests' isolation (test_ingestion_pipeline_durability.py): the
schema test database only, unique board tokens, job ids and run instants, a
check before writing that none of them exist and that no company is enabled
(a run disables every enabled company missing from its board file), and a
cleanup that deletes only rows carrying those identifiers, even when an
assertion fails.
"""

import io
import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import yaml
from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

from job_radar.cli import run_ingest
from tests.support.clock import FixedClock
from tests.support.pipeline import (
    SCHEMA_DB,
    FakeGreenhouse,
    Row,
    assert_run_is_consistent,
    assert_schema_test_database,
    board,
    counters,
    duplicate_identities,
    jobs_of,
    posting,
    results_of,
    run_row,
)

pytestmark = pytest.mark.integration

ALPHA = "cli-acceptance-alpha"
BETA = "cli-acceptance-beta"
TOKENS = (ALPHA, BETA)
ALPHA_IDS = (991_000_000_001, 991_000_000_002, 991_000_000_003)
BETA_IDS = (991_000_000_101, 991_000_000_102)
#: Distinctive instants: a run's started_at is its observed_at, so these
#: identify this module's runs and nothing else's.
FIRST = datetime(2032, 2, 2, 0, 0, 11, tzinfo=UTC)
SECOND = FIRST + timedelta(hours=1)
INSTANTS = (FIRST, SECOND)

#: SPEC, "Failure isolation": what every failure record must identify.
FAILURE_FIELDS = ("source", "board_token", "display_name", "operation", "error")


@dataclass
class Outcome:
    code: int
    stdout: str
    records: list[dict[str, object]]

    def failures(self) -> list[dict[str, object]]:
        return [r for r in self.records if r["level"] == "ERROR"]

    def completion(self) -> dict[str, object]:
        [record] = [r for r in self.records if r.get("operation") == "crawl_run"]
        return record


@dataclass
class Cli:
    greenhouse: FakeGreenhouse
    clock: FixedClock
    verifier: Engine
    config_path: Path

    def configure(self, *entries: tuple[str, str]) -> None:
        document = {
            "boards": [
                {
                    "source": "greenhouse",
                    "board_token": token,
                    "display_name": name,
                    "enabled": True,
                }
                for token, name in entries
            ]
        }
        self.config_path.write_text(yaml.safe_dump(document), encoding="utf-8")

    def run(self) -> Outcome:
        stdout, stderr = io.StringIO(), io.StringIO()
        code = run_ingest(
            stdout=stdout,
            stderr=stderr,
            transport=httpx.MockTransport(self.greenhouse.handle),
            clock=self.clock,
        )
        records = [json.loads(line) for line in stderr.getvalue().splitlines()]
        return Outcome(code, stdout.getvalue(), records)

    def run_id(self, instant: datetime) -> int:
        with self.verifier.connect() as fresh:
            [run_id] = (
                fresh.execute(
                    text("SELECT id FROM crawl_runs WHERE started_at = :instant"),
                    {"instant": instant},
                )
                .scalars()
                .all()
            )
        return int(run_id)


def _check_untouched(connection: Connection) -> None:
    assert_schema_test_database(connection)
    enabled = connection.execute(
        text("SELECT count(*) FROM companies WHERE is_enabled")
    ).scalar_one()
    ours = connection.execute(
        text(
            "SELECT"
            " (SELECT count(*) FROM companies WHERE board_token = ANY(:tokens))"
            " + (SELECT count(*) FROM jobs WHERE source = 'greenhouse'"
            "    AND source_job_id = ANY(:ids))"
            " + (SELECT count(*) FROM crawl_runs WHERE started_at = ANY(:instants))"
        ),
        {
            "tokens": list(TOKENS),
            "ids": [str(job_id) for job_id in (*ALPHA_IDS, *BETA_IDS)],
            "instants": list(INSTANTS),
        },
    ).scalar_one()
    if enabled or ours:
        pytest.fail(
            "refusing to commit: the schema test database already holds"
            f" {enabled} enabled companies and {ours} rows with this module's"
            " identifiers; a run would modify or collide with them"
        )


def _clean_up(connection: Connection) -> None:
    assert_schema_test_database(connection)
    company_ids = (
        connection.execute(
            text(
                "SELECT id FROM companies"
                " WHERE source = 'greenhouse' AND board_token = ANY(:tokens)"
            ),
            {"tokens": list(TOKENS)},
        )
        .scalars()
        .all()
    )
    # Children first: results and jobs reference companies with RESTRICT.
    connection.execute(
        text("DELETE FROM crawl_runs WHERE started_at = ANY(:instants)"),
        {"instants": list(INSTANTS)},
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
def cli(
    schema_database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Cli]:
    if make_url(schema_database_url).database != SCHEMA_DB:
        pytest.fail(f"refusing to commit outside {SCHEMA_DB}")
    config_path = tmp_path / "boards.yml"
    # The CLI reads its settings from the environment, as in production.
    monkeypatch.setenv("JOB_RADAR_DATABASE_URL", schema_database_url)
    monkeypatch.setenv("JOB_RADAR_BOARDS_CONFIG_PATH", str(config_path))
    verifier = create_engine(schema_database_url, poolclass=NullPool)
    try:
        with verifier.connect() as connection:
            _check_untouched(connection)
        try:
            yield Cli(FakeGreenhouse(), FixedClock(FIRST), verifier, config_path)
        finally:
            with verifier.begin() as connection:
                _clean_up(connection)
    finally:
        verifier.dispose()


def _jobs(cli: Cli, token: str) -> dict[str, Row]:
    with cli.verifier.connect() as fresh:
        return jobs_of(fresh, token)


# --- SPEC, "MVP Acceptance Criteria" ----------------------------------------------


def test_first_run_persists_every_job(cli: Cli) -> None:
    cli.configure((ALPHA, "CLI Alpha"))
    cli.greenhouse.serve(ALPHA, board(*(posting(i) for i in ALPHA_IDS)))

    outcome = cli.run()

    assert outcome.code == 0
    run_id = cli.run_id(FIRST)
    with cli.verifier.connect() as fresh:
        run = run_row(fresh, run_id)
        assert run["status"] == "success"
        assert counters(run) == (3, 3, 0, 0, 0)
        assert_run_is_consistent(fresh, run_id)
    rows = _jobs(cli, ALPHA)
    assert set(rows) == {str(i) for i in ALPHA_IDS}
    assert all(row["first_seen_at"] == FIRST for row in rows.values())
    assert outcome.stdout.splitlines()[0] == f"crawl run {run_id}: success"
    assert "totals: fetched=3 new=3 existing=0 updated=0 invalid=0" in outcome.stdout
    completion = outcome.completion()
    assert completion["status"] == "success"
    assert (completion["fetched"], completion["new"]) == (3, 3)
    assert outcome.failures() == []


def test_second_run_with_unchanged_data_is_idempotent(cli: Cli) -> None:
    cli.configure((ALPHA, "CLI Alpha"))
    cli.greenhouse.serve(ALPHA, board(*(posting(i) for i in ALPHA_IDS)))
    assert cli.run().code == 0
    cli.clock.set(SECOND)

    outcome = cli.run()

    assert outcome.code == 0
    run_id = cli.run_id(SECOND)
    with cli.verifier.connect() as fresh:
        run = run_row(fresh, run_id)
        assert run["status"] == "success"
        assert counters(run) == (3, 0, 3, 0, 0)
        assert duplicate_identities(fresh) == []
        assert_run_is_consistent(fresh, run_id)
    rows = _jobs(cli, ALPHA)
    assert len(rows) == len(ALPHA_IDS)
    assert all(row["first_seen_at"] == FIRST for row in rows.values())
    assert all(row["last_seen_at"] == SECOND for row in rows.values())
    assert "totals: fetched=3 new=0 existing=3 updated=0 invalid=0" in outcome.stdout


def test_one_failing_board_gives_partial_success_and_exit_zero(cli: Cli) -> None:
    cli.configure((ALPHA, "CLI Alpha"), (BETA, "CLI Beta"))
    cli.greenhouse.serve(ALPHA, board(*(posting(i) for i in ALPHA_IDS)))
    cli.greenhouse.serve(BETA, board(*(posting(i) for i in BETA_IDS)))
    assert cli.run().code == 0
    alpha_before = _jobs(cli, ALPHA)
    cli.greenhouse.serve(ALPHA, httpx.Response(500, text="provider error page"))
    cli.clock.set(SECOND)

    outcome = cli.run()

    assert outcome.code == 0
    run_id = cli.run_id(SECOND)
    with cli.verifier.connect() as fresh:
        assert run_row(fresh, run_id)["status"] == "partial_success"
        results = results_of(fresh, run_id)
        assert results[ALPHA]["status"] == "failed"
        assert counters(results[ALPHA]) == (0, 0, 0, 0, 0)
        assert results[ALPHA]["error_type"] == "CollectorError"
        assert results[BETA]["status"] == "success"
        assert counters(results[BETA]) == (2, 0, 2, 0, 0)
        assert_run_is_consistent(fresh, run_id)
    # The failing company's rows are byte-for-byte unchanged, xmin included.
    assert _jobs(cli, ALPHA) == alpha_before
    beta = _jobs(cli, BETA)
    assert set(beta) == {str(i) for i in BETA_IDS}
    assert all(row["last_seen_at"] == SECOND for row in beta.values())
    [failure] = outcome.failures()
    assert all(failure.get(name) for name in FAILURE_FIELDS)
    assert failure["board_token"] == ALPHA
    assert failure["display_name"] == "CLI Alpha"
    assert "provider error page" not in json.dumps(outcome.records)
    assert outcome.stdout.splitlines()[0] == f"crawl run {run_id}: partial_success"


def test_every_board_failing_records_failed_and_still_exits_zero(cli: Cli) -> None:
    # SPEC, "Run reporting": a completed run exits 0 whatever its status.
    cli.configure((ALPHA, "CLI Alpha"), (BETA, "CLI Beta"))
    cli.greenhouse.serve(ALPHA, httpx.Response(500))
    cli.greenhouse.serve(BETA, httpx.Response(503))

    outcome = cli.run()

    assert outcome.code == 0
    run_id = cli.run_id(FIRST)
    with cli.verifier.connect() as fresh:
        assert run_row(fresh, run_id)["status"] == "failed"
        results = results_of(fresh, run_id)
        assert {token: row["status"] for token, row in results.items()} == {
            ALPHA: "failed",
            BETA: "failed",
        }
        assert_run_is_consistent(fresh, run_id)
    assert _jobs(cli, ALPHA) == {}
    assert _jobs(cli, BETA) == {}
    failures = outcome.failures()
    assert sorted(str(f["board_token"]) for f in failures) == [ALPHA, BETA]
    assert all(all(f.get(name) for name in FAILURE_FIELDS) for f in failures)
    assert outcome.completion()["status"] == "failed"
    assert outcome.stdout.splitlines()[0] == f"crawl run {run_id}: failed"
