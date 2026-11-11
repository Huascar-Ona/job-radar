"""The real ingestion pipeline against PostgreSQL (ADR-0010).

Everything is real except the network: IngestionService, GreenhouseCollector,
GreenhouseNormalizer, the SQLAlchemy store adapter, the repositories and
PostgreSQL. HTTP goes to an ``httpx.MockTransport`` that serves canned boards
and fails the test on any request it was not told to expect.

Database safety. Every helper here writes only through a connection whose
database has been checked to be ``jobradar_schema_test``. Failure triggers are
created only inside a transaction that is always rolled back.
"""

import copy
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

import httpx
from sqlalchemy import Connection, text
from sqlalchemy.orm import Session, sessionmaker

from job_radar.collectors.greenhouse.collector import GreenhouseCollector
from job_radar.collectors.greenhouse.normalizer import GreenhouseNormalizer
from job_radar.config.boards import BoardsConfig
from job_radar.domain.job import JobSource
from job_radar.ingestion.service import IngestionService
from job_radar.ingestion.summary import CrawlRunSummary
from job_radar.persistence.ingestion_store import SqlAlchemyStoreFactory
from tests.support.clock import FixedClock

#: Must equal ``SCHEMA_DB`` in tests/conftest.py; checked against the live
#: connection before anything is written.
SCHEMA_DB: Final = "jobradar_schema_test"

GREENHOUSE_HOST: Final = "boards-api.greenhouse.io"

Row = dict[str, object]

_TEMPLATE: Final[dict[str, object]] = json.loads(
    (
        Path(__file__).resolve().parents[1]
        / "fixtures"
        / "greenhouse"
        / "board_jobs_content.json"
    ).read_text(encoding="utf-8")
)["jobs"][0]

_IDENTIFIER: Final = re.compile(r"[a-z][a-z0-9_]{0,62}")


# --- database identity ----------------------------------------------------------


def assert_schema_test_database(connection: Connection) -> None:
    """Refuse to go on unless this connection is to the schema test database."""
    name = connection.execute(text("SELECT current_database()")).scalar_one()
    if name != SCHEMA_DB:
        msg = f"refusing to write: connected to {name!r}, expected {SCHEMA_DB!r}"
        raise AssertionError(msg)


# --- source payloads ------------------------------------------------------------


def posting(job_id: object, **fields: object) -> dict[str, object]:
    """A Greenhouse posting shaped like the captured fixture.

    Timestamps carry an explicit offset so tests can assert their UTC values
    literally.
    """
    result = copy.deepcopy(_TEMPLATE)
    result["id"] = job_id
    result["absolute_url"] = f"https://jobs.example.test/{job_id}"
    result["first_published"] = "2026-11-01T10:00:00-05:00"
    result["updated_at"] = "2026-11-02T10:00:00-05:00"
    result.update(fields)
    return result


def board(*postings: object) -> dict[str, object]:
    """A Board API envelope whose ``meta.total`` matches its postings."""
    return {"jobs": list(postings), "meta": {"total": len(postings)}}


# --- HTTP ------------------------------------------------------------------------

Served = dict[str, object] | httpx.Response | httpx.HTTPError


@dataclass
class FakeGreenhouse:
    """Serves one response per board token and records every request.

    A request for a board it was not told about raises AssertionError, which
    is not an httpx error: the collector does not catch it, so the run fails
    loudly instead of the request reaching anything real.
    """

    boards: dict[str, Served] = field(default_factory=dict)
    requests: list[str] = field(default_factory=list)

    def serve(self, token: str, response: Served) -> None:
        self.boards[token] = response

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.host != GREENHOUSE_HOST:
            msg = f"unexpected host {request.url.host!r}"
            raise AssertionError(msg)
        token = request.url.path.split("/")[3]
        self.requests.append(token)
        if token not in self.boards:
            msg = f"unexpected request for board {token!r}"
            raise AssertionError(msg)
        served = self.boards[token]
        if isinstance(served, httpx.HTTPError):
            raise served
        if isinstance(served, httpx.Response):
            return served
        return httpx.Response(200, json=served)


# --- running the pipeline ---------------------------------------------------------


def run_pipeline(
    *,
    sessions: sessionmaker[Session],
    greenhouse: FakeGreenhouse,
    config: BoardsConfig,
    clock: FixedClock,
) -> CrawlRunSummary:
    """One real run: real collector, normalizer, adapter and repositories."""
    with httpx.Client(transport=httpx.MockTransport(greenhouse.handle)) as client:
        service = IngestionService(
            store=SqlAlchemyStoreFactory(sessions),
            collectors={JobSource.GREENHOUSE: GreenhouseCollector(client)},
            normalizers={JobSource.GREENHOUSE: GreenhouseNormalizer()},
            clock=clock,
        )
        return service.run(config)


def savepoint_sessions(connection: Connection) -> sessionmaker[Session]:
    """Sessions that join ``connection``'s transaction as SAVEPOINTs.

    Each service transaction becomes a SAVEPOINT: its commit releases it, its
    rollback rolls back to it. The outer transaction, owned by the test, is
    rolled back at the end, so nothing survives the test.
    """
    return sessionmaker(bind=connection, join_transaction_mode="create_savepoint")


# --- reading state back -----------------------------------------------------------


def _rows(connection: Connection, sql: str, **params: object) -> list[Row]:
    return [dict(row._mapping) for row in connection.execute(text(sql), params)]


def jobs_of(connection: Connection, token: str) -> dict[str, Row]:
    """A company's job rows by source_job_id, with ``xmin`` as ``row_version``.

    ``xmin`` is the id of the transaction that wrote the visible row version.
    Any write that survives, even one that leaves every value unchanged,
    makes a new version with a new ``xmin``; a write rolled back to a
    SAVEPOINT leaves the earlier version, and its ``xmin``, visible. Compare
    it only to show that no write to a row survived, never across a path that
    rewrites rows on purpose (a company conflict).
    """
    rows = _rows(
        connection,
        "SELECT j.*, j.xmin::text AS row_version FROM jobs j"
        " JOIN companies c ON c.id = j.company_id"
        " WHERE c.source = 'greenhouse' AND c.board_token = :token",
        token=token,
    )
    return {str(row["source_job_id"]): row for row in rows}


def company_row(connection: Connection, token: str) -> Row:
    [row] = _rows(
        connection,
        "SELECT *, xmin::text AS row_version FROM companies"
        " WHERE source = 'greenhouse' AND board_token = :token",
        token=token,
    )
    return row


def all_companies(connection: Connection) -> list[Row]:
    return _rows(
        connection,
        "SELECT *, xmin::text AS row_version FROM companies ORDER BY id",
    )


def run_row(connection: Connection, run_id: int) -> Row:
    [row] = _rows(connection, "SELECT * FROM crawl_runs WHERE id = :id", id=run_id)
    return row


def runs_after(connection: Connection, run_id: int) -> list[Row]:
    return _rows(
        connection,
        "SELECT * FROM crawl_runs WHERE id > :id ORDER BY id",
        id=run_id,
    )


def last_run_id(connection: Connection) -> int:
    value = connection.execute(
        text("SELECT coalesce(max(id), 0) FROM crawl_runs")
    ).scalar_one()
    return int(value)


def results_of(connection: Connection, run_id: int) -> dict[str, Row]:
    """A run's per-company result rows, by board token."""
    rows = _rows(
        connection,
        "SELECT r.*, c.board_token FROM crawl_company_results r"
        " JOIN companies c ON c.id = r.company_id"
        " WHERE r.crawl_run_id = :id",
        id=run_id,
    )
    return {str(row["board_token"]): row for row in rows}


def counters(row: Row) -> tuple[int, int, int, int, int]:
    """``(fetched, new, existing, updated, invalid)`` of a run or result row."""
    fetched, new, existing, updated, invalid = (
        int(str(row[f"jobs_{name}"]))
        for name in ("fetched", "new", "existing", "updated", "invalid")
    )
    return fetched, new, existing, updated, invalid


def duplicate_identities(connection: Connection) -> list[Row]:
    return _rows(
        connection,
        "SELECT source, source_job_id, count(*) AS n FROM jobs"
        " GROUP BY source, source_job_id HAVING count(*) > 1",
    )


def assert_run_is_consistent(connection: Connection, run_id: int) -> None:
    """SPEC "Run reporting" and "Crawl outcome model", read from the database.

    The run's counters equal the sum of its result rows, each row satisfies
    ``fetched = new + existing + invalid`` and ``updated <= existing``, a
    failed row has zero counters, and no job identity is stored twice.
    """
    run = run_row(connection, run_id)
    results = list(results_of(connection, run_id).values())
    assert run["companies_total"] == len(results)
    assert run["companies_succeeded"] == sum(r["status"] == "success" for r in results)
    assert run["companies_failed"] == sum(r["status"] == "failed" for r in results)
    sums = [sum(column) for column in zip(*(counters(r) for r in results), strict=True)]
    assert list(counters(run)) == (sums or [0, 0, 0, 0, 0])
    for result in results:
        fetched, new, existing, updated, invalid = counters(result)
        assert fetched == new + existing + invalid, result
        assert updated <= existing, result
        if result["status"] == "failed":
            assert counters(result) == (0, 0, 0, 0, 0), result
    assert duplicate_identities(connection) == []


# --- failure injection --------------------------------------------------------------


def install_failing_trigger(
    connection: Connection,
    *,
    name: str,
    table: str,
    event: str,
    condition: str,
    message: str = "'injected failure'",
) -> None:
    """Make PostgreSQL itself fail matching writes.

    Creates a plpgsql function and a ``BEFORE`` row trigger that raises
    ``P0001`` when ``condition`` holds. Only on the rollback-protected test
    connection: both disappear with the test's outer transaction. The
    arguments are fixed strings written in the tests, never data.
    """
    if not connection.in_transaction():
        msg = "failure triggers need a transaction that will be rolled back"
        raise AssertionError(msg)
    assert_schema_test_database(connection)
    if not _IDENTIFIER.fullmatch(name) or not _IDENTIFIER.fullmatch(table):
        msg = f"unsafe trigger or table name: {name!r}, {table!r}"
        raise AssertionError(msg)
    if event not in {"INSERT", "UPDATE", "INSERT OR UPDATE"}:
        msg = f"unsupported trigger event: {event!r}"
        raise AssertionError(msg)
    connection.execute(
        text(
            f"CREATE FUNCTION {name}() RETURNS trigger LANGUAGE plpgsql AS $$"
            f" BEGIN RAISE EXCEPTION USING MESSAGE = {message}, ERRCODE = 'P0001';"
            " END $$"
        )
    )
    connection.execute(
        text(
            f"CREATE TRIGGER {name} BEFORE {event} ON {table} FOR EACH ROW"
            f" WHEN ({condition}) EXECUTE FUNCTION {name}()"
        )
    )
