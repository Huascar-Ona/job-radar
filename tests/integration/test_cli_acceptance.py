"""Acceptance: the ingest and jobs commands end to end (ADR-0010, layer 4).

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

from job_radar.cli import JobsOptions, OutputFormat, run_ingest, run_jobs
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
#: Ten jobs, for the large-disappearance guard (ADR-0017).
MANY_IDS = tuple(991_000_000_201 + i for i in range(10))
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

    def jobs(
        self,
        *,
        new_in_last_run: bool = False,
        company: str | None = None,
        output: OutputFormat = "table",
    ) -> Outcome:
        """``job-radar jobs`` through its composition root, read-only."""
        stdout, stderr = io.StringIO(), io.StringIO()
        code = run_jobs(
            stdout=stdout,
            stderr=stderr,
            options=JobsOptions(
                new_in_last_run=new_in_last_run, company=company, output=output
            ),
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
            "ids": [str(job_id) for job_id in (*ALPHA_IDS, *BETA_IDS, *MANY_IDS)],
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


# --- SPEC, "Job listing and query" -------------------------------------------------


def _listed(outcome: Outcome) -> list[dict[str, object]]:
    return [json.loads(line) for line in outcome.stdout.splitlines()]


def _context(outcome: Outcome) -> dict[str, object]:
    [record] = [
        r
        for r in outcome.records
        if r["message"] == "selected the latest finished crawl run"
    ]
    return record


def test_jobs_lists_exactly_the_jobs_the_last_run_inserted(
    cli: Cli, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cli.configure((ALPHA, "CLI Alpha"))
    cli.greenhouse.serve(ALPHA, board(*(posting(i) for i in ALPHA_IDS)))
    assert cli.run().code == 0
    # Only the database URL is read: broken ingestion settings cannot matter.
    monkeypatch.setenv("JOB_RADAR_HTTP_TIMEOUT_SECONDS", "-1")
    monkeypatch.setenv("JOB_RADAR_BOARDS_CONFIG_PATH", str(tmp_path / "missing.yml"))

    outcome = cli.jobs(new_in_last_run=True, output="jsonl")

    assert outcome.code == 0
    listed = _listed(outcome)
    # Same first_seen_at, so the newest id, the last one inserted, comes first.
    assert [job["source_job_id"] for job in listed] == [
        str(i) for i in reversed(ALPHA_IDS)
    ]
    assert {job["company"] for job in listed} == {"CLI Alpha"}
    assert {job["board_token"] for job in listed} == {ALPHA}
    assert {job["first_seen_at"] for job in listed} == {
        FIRST.isoformat(timespec="microseconds")
    }
    context = _context(outcome)
    assert context["run_id"] == cli.run_id(FIRST)
    assert (context["status"], context["new"]) == ("success", 3)
    assert context["later_unfinished_runs"] == 0
    assert outcome.failures() == []


def test_jobs_after_an_unchanged_second_run_lists_nothing_new(cli: Cli) -> None:
    cli.configure((ALPHA, "CLI Alpha"))
    cli.greenhouse.serve(ALPHA, board(*(posting(i) for i in ALPHA_IDS)))
    assert cli.run().code == 0
    cli.clock.set(SECOND)
    assert cli.run().code == 0

    newest = cli.jobs(new_in_last_run=True)
    history = cli.jobs(company=ALPHA, output="jsonl")

    assert newest.code == 0
    assert newest.stdout == (
        f"crawl run {cli.run_id(SECOND)} (success),"
        f" started {SECOND.isoformat(timespec='microseconds')},"
        " 0 new jobs recorded\n"
        "No matching jobs.\n"
    )
    assert history.code == 0
    listed = _listed(history)
    assert sorted(str(job["source_job_id"]) for job in listed) == sorted(
        str(i) for i in ALPHA_IDS
    )
    assert {job["first_seen_at"] for job in listed} == {
        FIRST.isoformat(timespec="microseconds")
    }
    assert {job["last_seen_at"] for job in listed} == {
        SECOND.isoformat(timespec="microseconds")
    }


def test_jobs_new_in_a_partial_run_excludes_the_failing_company(cli: Cli) -> None:
    cli.configure((ALPHA, "CLI Alpha"), (BETA, "CLI Beta"))
    cli.greenhouse.serve(ALPHA, httpx.Response(500))
    cli.greenhouse.serve(BETA, board(*(posting(i) for i in BETA_IDS)))
    assert cli.run().code == 0

    outcome = cli.jobs(new_in_last_run=True, output="jsonl")

    assert outcome.code == 0
    listed = _listed(outcome)
    assert sorted(str(job["source_job_id"]) for job in listed) == sorted(
        str(i) for i in BETA_IDS
    )
    assert {job["board_token"] for job in listed} == {BETA}
    context = _context(outcome)
    assert (context["status"], context["new"]) == ("partial_success", 2)


# --- ADR-0017, deactivation on verified absence ------------------------------------


def _active(cli: Cli, token: str) -> list[str]:
    return sorted(k for k, row in _jobs(cli, token).items() if row["is_active"])


def test_ingest_deactivates_a_job_closed_on_a_verified_board(cli: Cli) -> None:
    cli.configure((ALPHA, "CLI Alpha"))
    cli.greenhouse.serve(ALPHA, board(*(posting(i) for i in ALPHA_IDS)))
    assert cli.run().code == 0
    cli.clock.set(SECOND)
    kept = ALPHA_IDS[:2]
    cli.greenhouse.serve(ALPHA, board(*(posting(i) for i in kept)))

    outcome = cli.run()

    assert outcome.code == 0
    run_id = cli.run_id(SECOND)
    with cli.verifier.connect() as fresh:
        result = results_of(fresh, run_id)[ALPHA]
        assert result["status"] == "success"
        assert result["jobs_deactivated"] == 1
        assert result["completeness_verified"] is True
        assert run_row(fresh, run_id)["jobs_deactivated"] == 1
        assert_run_is_consistent(fresh, run_id)
    rows = _jobs(cli, ALPHA)
    closed = rows[str(ALPHA_IDS[2])]
    assert closed["is_active"] is False
    assert closed["deactivated_at"] == SECOND
    assert closed["last_seen_at"] == FIRST, "the row and its history remain"
    assert "deactivated=1" in outcome.stdout
    assert outcome.completion()["deactivated"] == 1
    # The listing shows active jobs only.
    listed = _listed(cli.jobs(company=ALPHA, output="jsonl"))
    assert sorted(str(job["source_job_id"]) for job in listed) == sorted(
        str(i) for i in kept
    )


def test_ingest_skips_a_large_disappearance_and_still_succeeds(cli: Cli) -> None:
    cli.configure((ALPHA, "CLI Alpha"))
    cli.greenhouse.serve(ALPHA, board(*(posting(i) for i in MANY_IDS)))
    assert cli.run().code == 0
    cli.clock.set(SECOND)
    cli.greenhouse.serve(ALPHA, board(*(posting(i) for i in MANY_IDS[:4])))

    outcome = cli.run()  # 6 of 10 previously active jobs vanished

    assert outcome.code == 0
    run_id = cli.run_id(SECOND)
    with cli.verifier.connect() as fresh:
        result = results_of(fresh, run_id)[ALPHA]
        assert result["status"] == "success"
        assert result["jobs_deactivated"] == 0
        assert counters(result) == (4, 0, 4, 0, 0)
        assert_run_is_consistent(fresh, run_id)
    assert _active(cli, ALPHA) == sorted(str(i) for i in MANY_IDS)
    [skip] = [r for r in outcome.records if "deactivation_skipped" in r]
    assert skip["level"] == "WARNING"
    assert skip["deactivation_skipped"] == "large_disappearance"
    assert (skip["absent"], skip["previously_active"]) == (6, 10)
    assert outcome.failures() == []


def test_ingest_does_not_deactivate_without_a_verified_total(cli: Cli) -> None:
    cli.configure((ALPHA, "CLI Alpha"))
    cli.greenhouse.serve(ALPHA, board(*(posting(i) for i in ALPHA_IDS)))
    assert cli.run().code == 0
    cli.clock.set(SECOND)
    cli.greenhouse.serve(ALPHA, {"jobs": [posting(ALPHA_IDS[0])]})

    outcome = cli.run()

    assert outcome.code == 0
    assert _active(cli, ALPHA) == sorted(str(i) for i in ALPHA_IDS)
    with cli.verifier.connect() as fresh:
        result = results_of(fresh, cli.run_id(SECOND))[ALPHA]
        assert result["completeness_verified"] is False
        assert result["jobs_deactivated"] == 0
