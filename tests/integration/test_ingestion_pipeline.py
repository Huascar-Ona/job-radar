"""The ingestion pipeline end to end against PostgreSQL.

Real IngestionService, GreenhouseCollector, GreenhouseNormalizer, store
adapter, repositories and database; only HTTP is mocked. Every assertion that
matters reads the database back. Expected values come from the scenario (what
was served, which statuses and counts it implies), never from recomputing the
implementation's own logic.

Each test runs inside one transaction that is rolled back
(``pipeline_connection``); the service's transactions are SAVEPOINTs within it.
"""

import json
import logging
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import Connection, text

from job_radar.config.boards import BoardsConfig
from job_radar.ingestion.service import LOGGER_NAME
from job_radar.ingestion.summary import CrawlRunSummary
from tests.support.clock import FixedClock
from tests.support.ingestion_fakes import boards
from tests.support.pipeline import (
    FakeGreenhouse,
    all_companies,
    assert_run_is_consistent,
    board,
    company_row,
    counters,
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
T1 = T0 + timedelta(hours=1)
PUBLISHED = datetime(2026, 11, 1, 15, 0, tzinfo=UTC)  # 10:00 at -05:00
UPDATED = datetime(2026, 11, 2, 15, 0, tzinfo=UTC)
NUL = "\x00"


class Pipeline:
    def __init__(self, connection: Connection) -> None:
        self.connection = connection
        self.greenhouse = FakeGreenhouse()
        self.clock = FixedClock(T0)

    def run(self, config: BoardsConfig) -> CrawlRunSummary:
        summary = run_pipeline(
            sessions=savepoint_sessions(self.connection),
            greenhouse=self.greenhouse,
            config=config,
            clock=self.clock,
        )
        assert_run_is_consistent(self.connection, summary.run_id)
        return summary

    def later(self) -> None:
        self.clock.advance(timedelta(hours=1))
        self.greenhouse.requests.clear()


@pytest.fixture
def pipeline(pipeline_connection: Connection) -> Pipeline:
    return Pipeline(pipeline_connection)


# --- first run and re-observation ----------------------------------------------


def test_a_first_run_stores_every_posting(pipeline: Pipeline) -> None:
    served = [posting(101), posting(102, title="Data Engineer"), posting(103)]
    pipeline.greenhouse.serve("alpha", board(*served))

    summary = pipeline.run(boards(("alpha", "Alpha")))

    conn = pipeline.connection
    rows = jobs_of(conn, "alpha")
    assert set(rows) == {"101", "102", "103"}
    job = rows["102"]
    assert job["title"] == "Data Engineer"
    assert job["job_url"] == "https://jobs.example.test/102"
    assert job["source_published_at"] == PUBLISHED
    assert job["source_updated_at"] == UPDATED
    assert job["first_seen_at"] == T0
    assert job["last_seen_at"] == T0
    assert job["is_active"] is True
    assert job["previous_content_hash"] is None
    assert job["raw_data"] == served[1]

    run = run_row(conn, summary.run_id)
    assert run["status"] == "success"
    assert run["started_at"] == T0
    assert run["finished_at"] is not None
    assert (
        run["companies_total"],
        run["companies_succeeded"],
        run["companies_failed"],
    ) == (1, 1, 0)
    assert counters(run) == (3, 3, 0, 0, 0)
    result = results_of(conn, summary.run_id)["alpha"]
    assert result["status"] == "success"
    assert counters(result) == (3, 3, 0, 0, 0)
    assert result["error_type"] is None
    assert result["error_message"] is None


def test_an_unchanged_rerun_adds_nothing(pipeline: Pipeline) -> None:
    served = board(posting(101), posting(102), posting(103))
    pipeline.greenhouse.serve("alpha", served)
    pipeline.run(boards(("alpha", "Alpha")))
    first = jobs_of(pipeline.connection, "alpha")
    pipeline.later()

    summary = pipeline.run(boards(("alpha", "Alpha")))

    rows = jobs_of(pipeline.connection, "alpha")
    assert set(rows) == {"101", "102", "103"}
    for job_id, job in rows.items():
        assert job["first_seen_at"] == T0
        assert job["last_seen_at"] == T1
        assert job["raw_data"] == first[job_id]["raw_data"]
        assert job["content_hash"] == first[job_id]["content_hash"]
        assert job["previous_content_hash"] == first[job_id]["content_hash"]
    result = results_of(pipeline.connection, summary.run_id)["alpha"]
    assert counters(result) == (3, 0, 3, 0, 0)


def test_a_changed_posting_is_updated_in_place(pipeline: Pipeline) -> None:
    pipeline.greenhouse.serve("alpha", board(posting(101), posting(102)))
    pipeline.run(boards(("alpha", "Alpha")))
    before = jobs_of(pipeline.connection, "alpha")
    pipeline.later()
    changed = posting(102, title="Staff Data Engineer")
    pipeline.greenhouse.serve("alpha", board(posting(101), changed))

    summary = pipeline.run(boards(("alpha", "Alpha")))

    rows = jobs_of(pipeline.connection, "alpha")
    assert set(rows) == {"101", "102"}
    assert rows["102"]["title"] == "Staff Data Engineer"
    assert rows["102"]["raw_data"] == changed
    assert rows["102"]["content_hash"] != before["102"]["content_hash"]
    assert rows["102"]["previous_content_hash"] == before["102"]["content_hash"]
    assert rows["102"]["first_seen_at"] == T0
    assert rows["101"]["content_hash"] == before["101"]["content_hash"]
    result = results_of(pipeline.connection, summary.run_id)["alpha"]
    assert counters(result) == (2, 0, 2, 1, 0)


def test_a_change_to_an_unmapped_field_counts_as_updated(pipeline: Pipeline) -> None:
    # The content hash covers raw_data as well as the normalized fields
    # (ADR-0011 erratum), so a field Job Radar does not normalize still
    # counts, and replaces the stored payload.
    pipeline.greenhouse.serve("alpha", board(posting(101, internal_job_id=1)))
    pipeline.run(boards(("alpha", "Alpha")))
    pipeline.later()
    changed = posting(101, internal_job_id=2)
    pipeline.greenhouse.serve("alpha", board(changed))

    summary = pipeline.run(boards(("alpha", "Alpha")))

    assert jobs_of(pipeline.connection, "alpha")["101"]["raw_data"] == changed
    result = results_of(pipeline.connection, summary.run_id)["alpha"]
    assert counters(result) == (1, 0, 1, 1, 0)


# --- duplicates and response order -------------------------------------------------


def test_a_repeated_id_ends_with_its_last_occurrence(pipeline: Pipeline) -> None:
    last = posting(201, title="Second version")
    pipeline.greenhouse.serve(
        "alpha", board(posting(201, title="First version"), posting(202), last)
    )

    summary = pipeline.run(boards(("alpha", "Alpha")))

    rows = jobs_of(pipeline.connection, "alpha")
    assert set(rows) == {"201", "202"}
    assert rows["201"]["title"] == "Second version"
    assert rows["201"]["raw_data"] == last
    assert rows["201"]["first_seen_at"] == T0
    result = results_of(pipeline.connection, summary.run_id)["alpha"]
    assert counters(result) == (3, 2, 1, 1, 0)


def test_an_invalid_repeat_does_not_replace_the_valid_occurrence(
    pipeline: Pipeline,
) -> None:
    first = posting(301)
    pipeline.greenhouse.serve(
        "alpha", board(first, posting(301, updated_at="not a timestamp"))
    )

    summary = pipeline.run(boards(("alpha", "Alpha")))

    assert jobs_of(pipeline.connection, "alpha")["301"]["raw_data"] == first
    result = results_of(pipeline.connection, summary.run_id)["alpha"]
    assert counters(result) == (2, 1, 0, 0, 1)


# --- invalid postings, all-invalid and empty boards --------------------------------


def test_invalid_postings_are_skipped_and_the_rest_stored(pipeline: Pipeline) -> None:
    pipeline.greenhouse.serve(
        "alpha",
        board(
            posting(401),
            posting("402"),  # a string id
            posting(403, updated_at="2026-11-01T10:00:00"),  # no offset
            posting(404),
        ),
    )

    summary = pipeline.run(boards(("alpha", "Alpha")))

    assert set(jobs_of(pipeline.connection, "alpha")) == {"401", "404"}
    result = results_of(pipeline.connection, summary.run_id)["alpha"]
    assert result["status"] == "success"
    assert counters(result) == (4, 2, 0, 0, 2)


def test_text_postgresql_cannot_store_makes_a_posting_invalid_not_the_company(
    pipeline: Pipeline,
) -> None:
    # Before the NUL rule (SPEC, "Text PostgreSQL cannot store") each of these
    # postings failed the company's whole transaction.
    pipeline.greenhouse.serve(
        "alpha",
        board(
            posting(451),
            posting(452, title=f"Engineer{NUL}"),
            posting(453, content=f"&lt;p&gt;{NUL}&lt;/p&gt;"),
            posting(454, metadata=[{"value": f"x{NUL}"}]),
            posting(455, offices=[{f"name{NUL}": "HQ"}]),
            posting(456),
        ),
    )

    summary = pipeline.run(boards(("alpha", "Alpha")))

    assert set(jobs_of(pipeline.connection, "alpha")) == {"451", "456"}
    result = results_of(pipeline.connection, summary.run_id)["alpha"]
    assert result["status"] == "success"
    assert counters(result) == (6, 2, 0, 0, 4)


SURROGATE = "\ud800"  # unpaired: a JSON `\ud800` escape without its partner
ROCKET = "\U0001f680"  # non-BMP; on the wire, the escaped pair 🚀


def as_wire_json(envelope: dict[str, object]) -> httpx.Response:
    """The envelope as the Board API would send it: ASCII JSON with escapes.

    ``httpx.Response(json=...)`` encodes as UTF-8, which cannot carry an
    unpaired surrogate and would fail inside this fake instead of exercising
    the collector. Real JSON can only carry one as a ``\\uD800`` escape.
    """
    body = json.dumps(envelope).encode("ascii")
    assert b"\\ud800" in body  # the scenario under test reaches the wire
    return httpx.Response(
        200, content=body, headers={"Content-Type": "application/json"}
    )


def rejections(caplog: pytest.LogCaptureFixture) -> dict[str, str]:
    """Logged normalization rejections: source_job_id -> sanitized reason."""
    return {
        str(getattr(record, "source_job_id", None)): str(getattr(record, "error", ""))
        for record in caplog.records
        if getattr(record, "operation", None) == "normalize"
    }


def test_an_unpaired_surrogate_in_a_text_field_makes_only_that_posting_invalid(
    pipeline: Pipeline, caplog: pytest.LogCaptureFixture
) -> None:
    # Before 4D-5 an optional text field accepted it. psycopg's text dumper
    # raises UnicodeEncodeError for it, which is not a database error, so it
    # would have escaped the service instead of failing one posting (SPEC,
    # "Text PostgreSQL cannot store").
    caplog.set_level(logging.WARNING, logger=LOGGER_NAME)
    invalid = posting(461, location={"name": f"Remote{SURROGATE}"})
    valid = posting(462, title=f"Engineer {ROCKET}")
    pipeline.greenhouse.serve("alpha", as_wire_json(board(invalid, valid)))

    summary = pipeline.run(boards(("alpha", "Alpha")))

    rows = jobs_of(pipeline.connection, "alpha")
    assert set(rows) == {"462"}
    assert rows["462"]["title"] == f"Engineer {ROCKET}"
    assert rows["462"]["raw_data"] == valid
    assert run_row(pipeline.connection, summary.run_id)["status"] == "success"
    result = results_of(pipeline.connection, summary.run_id)["alpha"]
    assert result["status"] == "success"
    assert counters(result) == (2, 1, 0, 0, 1)
    assert rejections(caplog) == {
        "461": "location: surrogate_character (got str); "
        "raw_data: surrogate_character (got dict)"
    }
    assert SURROGATE not in caplog.text


def test_an_unpaired_surrogate_only_in_nested_raw_data_makes_only_that_posting_invalid(
    pipeline: Pipeline, caplog: pytest.LogCaptureFixture
) -> None:
    # An unmapped field never reaches a text column; without the check it
    # would reach `jsonb` as a `\ud800` escape.
    caplog.set_level(logging.WARNING, logger=LOGGER_NAME)
    pipeline.greenhouse.serve(
        "alpha",
        as_wire_json(
            board(
                posting(471, metadata=[{"name": "Team", "value": f"x{SURROGATE}"}]),
                posting(472),
            )
        ),
    )

    summary = pipeline.run(boards(("alpha", "Alpha")))

    assert set(jobs_of(pipeline.connection, "alpha")) == {"472"}
    assert run_row(pipeline.connection, summary.run_id)["status"] == "success"
    result = results_of(pipeline.connection, summary.run_id)["alpha"]
    assert result["status"] == "success"
    assert counters(result) == (2, 1, 0, 0, 1)
    assert rejections(caplog) == {"471": "raw_data: surrogate_character (got dict)"}
    assert SURROGATE not in caplog.text


def test_a_board_of_only_invalid_postings_still_succeeds(pipeline: Pipeline) -> None:
    pipeline.greenhouse.serve(
        "alpha", board(posting("not-an-id"), posting(502, title=None))
    )

    summary = pipeline.run(boards(("alpha", "Alpha")))

    assert jobs_of(pipeline.connection, "alpha") == {}
    assert run_row(pipeline.connection, summary.run_id)["status"] == "success"
    result = results_of(pipeline.connection, summary.run_id)["alpha"]
    assert result["status"] == "success"
    assert counters(result) == (2, 0, 0, 0, 2)


def test_an_empty_board_succeeds_with_zero_counters(pipeline: Pipeline) -> None:
    pipeline.greenhouse.serve("alpha", board())

    summary = pipeline.run(boards(("alpha", "Alpha")))

    assert jobs_of(pipeline.connection, "alpha") == {}
    result = results_of(pipeline.connection, summary.run_id)["alpha"]
    assert result["status"] == "success"
    assert counters(result) == (0, 0, 0, 0, 0)


# --- identity conflicts ----------------------------------------------------------


def test_a_job_claimed_by_another_company_stays_where_it_is(
    pipeline: Pipeline,
) -> None:
    original = posting(601, title="Original")
    pipeline.greenhouse.serve("alpha", board(original))
    pipeline.run(boards(("alpha", "Alpha")))
    alpha_id = company_row(pipeline.connection, "alpha")["id"]
    pipeline.later()
    pipeline.greenhouse.serve("alpha", board())
    pipeline.greenhouse.serve("beta", board(posting(601, title="Hijack")))

    summary = pipeline.run(boards(("alpha", "Alpha"), ("beta", "Beta")))

    # Values, not row versions: the conflict path rewrites the row with its
    # own values on purpose (ADR-0015), so xmin changes by design.
    [job] = jobs_of(pipeline.connection, "alpha").values()
    assert job["company_id"] == alpha_id
    assert job["title"] == "Original"
    assert job["raw_data"] == original
    assert job["last_seen_at"] == T0
    assert job["previous_content_hash"] is None
    assert jobs_of(pipeline.connection, "beta") == {}
    beta = results_of(pipeline.connection, summary.run_id)["beta"]
    assert beta["status"] == "success"
    assert counters(beta) == (1, 0, 0, 0, 1)


# --- collector failures ---------------------------------------------------------


def test_a_failing_board_leaves_its_jobs_untouched_and_others_proceed(
    pipeline: Pipeline,
) -> None:
    pipeline.greenhouse.serve("alpha", board(posting(701), posting(702)))
    pipeline.greenhouse.serve("beta", board(posting(711)))
    pipeline.run(boards(("alpha", "Alpha"), ("beta", "Beta")))
    alpha_before = jobs_of(pipeline.connection, "alpha")
    pipeline.later()
    pipeline.greenhouse.serve("alpha", httpx.Response(503, text="maintenance"))
    pipeline.greenhouse.serve("beta", board(posting(711), posting(712)))

    summary = pipeline.run(boards(("alpha", "Alpha"), ("beta", "Beta")))

    # A collector failure writes nothing for that company, so every row,
    # row version included, is exactly as before.
    assert jobs_of(pipeline.connection, "alpha") == alpha_before
    assert set(jobs_of(pipeline.connection, "beta")) == {"711", "712"}
    results = results_of(pipeline.connection, summary.run_id)
    assert results["alpha"]["status"] == "failed"
    assert counters(results["alpha"]) == (0, 0, 0, 0, 0)
    assert results["alpha"]["error_type"] == "CollectorError"
    assert results["alpha"]["error_message"] == "HTTP 503"
    assert results["beta"]["status"] == "success"
    run = run_row(pipeline.connection, summary.run_id)
    assert run["status"] == "partial_success"
    assert (
        run["companies_total"],
        run["companies_succeeded"],
        run["companies_failed"],
    ) == (2, 1, 1)
    assert counters(run) == (2, 1, 1, 0, 0)


def test_a_run_where_every_board_fails_is_failed(pipeline: Pipeline) -> None:
    pipeline.greenhouse.serve("alpha", httpx.Response(404, json={"error": "gone"}))
    pipeline.greenhouse.serve("beta", httpx.ConnectError("unreachable"))

    summary = pipeline.run(boards(("alpha", "Alpha"), ("beta", "Beta")))

    run = run_row(pipeline.connection, summary.run_id)
    assert run["status"] == "failed"
    assert (run["companies_succeeded"], run["companies_failed"]) == (0, 2)
    results = results_of(pipeline.connection, summary.run_id)
    assert results["alpha"]["error_message"] == "HTTP 404"
    assert results["beta"]["error_message"] == "transport error (ConnectError)"
    assert jobs_of(pipeline.connection, "alpha") == {}
    assert jobs_of(pipeline.connection, "beta") == {}


def test_a_run_with_no_enabled_companies_succeeds_empty(pipeline: Pipeline) -> None:
    summary = pipeline.run(boards())

    run = run_row(pipeline.connection, summary.run_id)
    assert run["status"] == "success"
    assert run["companies_total"] == 0
    assert counters(run) == (0, 0, 0, 0, 0)
    assert results_of(pipeline.connection, summary.run_id) == {}
    assert pipeline.greenhouse.requests == []


# --- configuration reconciliation ------------------------------------------------


def test_reconciliation_follows_the_configuration(pipeline: Pipeline) -> None:
    for token, job_id in (("alpha", 801), ("beta", 811), ("gamma", 821)):
        pipeline.greenhouse.serve(token, board(posting(job_id)))
    pipeline.run(boards(("alpha", "Alpha"), ("beta", "Beta"), ("gamma", "Gamma")))
    beta_jobs = jobs_of(pipeline.connection, "beta")
    gamma_jobs = jobs_of(pipeline.connection, "gamma")
    pipeline.later()

    summary = pipeline.run(
        boards(("alpha", "Alpha Inc"), ("beta", "Beta", False))  # gamma removed
    )

    conn = pipeline.connection
    assert company_row(conn, "alpha")["display_name"] == "Alpha Inc"
    assert company_row(conn, "alpha")["is_enabled"] is True
    assert company_row(conn, "beta")["is_enabled"] is False
    assert company_row(conn, "gamma")["is_enabled"] is False  # disabled, not deleted
    assert set(results_of(conn, summary.run_id)) == {"alpha"}
    assert pipeline.greenhouse.requests == ["alpha"]
    # Disabling is not deactivation (ADR-0004): no write reached these rows.
    assert jobs_of(conn, "beta") == beta_jobs
    assert jobs_of(conn, "gamma") == gamma_jobs


def test_an_unwired_source_rolls_reconciliation_back_and_opens_no_run(
    pipeline: Pipeline,
) -> None:
    conn = pipeline.connection
    conn.execute(
        text(
            "INSERT INTO companies (source, board_token, display_name)"
            " VALUES ('lever', 'elsewhere', 'Elsewhere')"
        )
    )
    companies_before = all_companies(conn)
    runs_before = last_run_id(conn)

    with pytest.raises(ValueError, match="lever"):
        pipeline.run(boards(("alpha", "Alpha")))

    # Reconciliation's writes (alpha's insert) were rolled back to its
    # SAVEPOINT, so the visible rows and their versions are those from before.
    assert all_companies(conn) == companies_before
    assert runs_after(conn, runs_before) == []
    assert pipeline.greenhouse.requests == []
