"""``job-radar jobs``: parsing, selection, rendering, sanitization, failures.

No database and no network. ``JobQuery`` is replaced by a fake that records
what it was asked; the engine is real but never connects, because nothing
executes a statement on it. The query itself is exercised against PostgreSQL
by tests/integration/test_job_query.py, and the command end to end by
tests/integration/test_cli_acceptance.py.
"""

import io
import json
import logging
import unicodedata
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from job_radar import cli
from job_radar.domain.crawl import CrawlRunStatus
from job_radar.persistence.job_query import (
    FinishedRun,
    JobCriteria,
    JobPage,
    ListedJob,
)
from job_radar.persistence.session import create_read_only_engine, read_only_session

STARTED = datetime(2026, 11, 12, 18, 4, 11, 123456, tzinfo=UTC)
SECRET_URL = "postgresql+psycopg://scott:hunter2-secret@db.internal:5432/jobradar"

#: Every character class the table must neutralize: ANSI clear screen, the
#: one-byte CSI, a bidi override and isolate, a line separator, CR LF, BEL,
#: a zero-width space and a byte-order mark.
HOSTILE = (
    "Eng\x1b[2Jineer\x9b31m \u202eevil\u202c \u2066iso\u2069"
    "\u2028next\r\nline\x07\u200bzw\ufeff"
)


#: Stated independently of cli.UNSAFE_CATEGORIES, so the tests do not trust
#: the code they check: controls, format characters, surrogates, and line and
#: paragraph separators.
UNSAFE = frozenset({"Cc", "Cf", "Cs", "Zl", "Zp"})


def job(
    source_job_id: str = "1",
    *,
    company: str = "Acme",
    title: str = "Backend Engineer",
    location: str | None = "Remote, Mexico",
    department: str | None = None,
    first_seen_at: datetime = STARTED,
    job_url: str | None = None,
) -> ListedJob:
    return ListedJob(
        source="greenhouse",
        source_job_id=source_job_id,
        company=company,
        board_token="acme",
        title=title,
        location=location,
        department=department,
        job_url=job_url or f"https://boards.example/acme/jobs/{source_job_id}",
        apply_url=None,
        source_published_at=None,
        source_updated_at=None,
        first_seen_at=first_seen_at,
        last_seen_at=first_seen_at + timedelta(hours=1),
        is_active=True,
    )


def page(*jobs: ListedJob, more: bool = False) -> JobPage:
    return JobPage(jobs=jobs, more_available=more)


def finished(
    status: CrawlRunStatus = CrawlRunStatus.SUCCESS, new: int = 1
) -> FinishedRun:
    return FinishedRun(run_id=42, status=status, started_at=STARTED, jobs_new=new)


# --- fakes ----------------------------------------------------------------------


@dataclass
class FakeData:
    """What the fake query returns, and what it was asked."""

    page: JobPage = field(default_factory=lambda: page(job()))
    run: FinishedRun | None = field(default_factory=finished)
    unfinished: int = 0
    error: Exception | None = None
    criteria: list[JobCriteria] = field(default_factory=list)
    unfinished_after: list[int | None] = field(default_factory=list)
    run_lookups: int = 0


class FakeJobQuery:
    data: FakeData

    def __init__(self, session: object) -> None:
        self.session = session

    def _maybe_fail(self) -> None:
        if self.data.error is not None:
            raise self.data.error

    def latest_finished_run(self) -> FinishedRun | None:
        self._maybe_fail()
        self.data.run_lookups += 1
        return self.data.run

    def unfinished_runs_after(self, run_id: int | None) -> int:
        self.data.unfinished_after.append(run_id)
        return self.data.unfinished

    def list(self, criteria: JobCriteria) -> JobPage:
        self._maybe_fail()
        self.data.criteria.append(criteria)
        return self.data.page


@dataclass
class Engines:
    created: list[Engine] = field(default_factory=list)
    disposed: list[Engine] = field(default_factory=list)


@pytest.fixture(autouse=True)
def data(monkeypatch: pytest.MonkeyPatch) -> FakeData:
    recorded = FakeData()
    monkeypatch.setattr(cli, "JobQuery", FakeJobQuery)
    monkeypatch.setattr(FakeJobQuery, "data", recorded, raising=False)
    return recorded


@pytest.fixture(autouse=True)
def engines(monkeypatch: pytest.MonkeyPatch) -> Engines:
    """Record every engine `jobs` creates, and whether it was disposed."""
    recorded = Engines()

    def factory(url: str) -> Engine:
        engine = create_read_only_engine(url)
        recorded.created.append(engine)
        original = engine.dispose

        def dispose(close: bool = True) -> None:
            recorded.disposed.append(engine)
            original(close)

        monkeypatch.setattr(engine, "dispose", dispose)
        return engine

    monkeypatch.setattr(cli, "create_read_only_engine", factory)
    return recorded


@pytest.fixture(autouse=True)
def no_ingestion(monkeypatch: pytest.MonkeyPatch) -> None:
    """`jobs` must never build an HTTP client or read the board file."""

    def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("jobs touched ingestion")

    monkeypatch.setattr(cli, "create_client", forbidden)
    monkeypatch.setattr(cli, "load_boards_config", forbidden)
    monkeypatch.setattr(cli, "IngestionService", forbidden)


@pytest.fixture(autouse=True)
def logging_is_restored() -> Iterator[None]:
    logger = logging.getLogger(cli.APP_LOGGER)
    handlers, level = list(logger.handlers), logger.level
    yield
    assert logger.handlers == handlers
    assert logger.level == level


@dataclass
class Output:
    code: int
    stdout: str
    records: list[dict[str, object]]


def jobs(**options: object) -> Output:
    stdout, stderr = io.StringIO(), io.StringIO()
    code = cli.run_jobs(
        stdout=stdout,
        stderr=stderr,
        options=cli.JobsOptions(**options),  # type: ignore[arg-type]
    )
    records = [json.loads(line) for line in stderr.getvalue().splitlines()]
    return Output(code, stdout.getvalue(), records)


def parse(argv: list[str]) -> cli.JobsOptions:
    captured: list[cli.JobsOptions] = []

    def fake_run_jobs(**kwargs: object) -> int:
        options = kwargs["options"]
        assert isinstance(options, cli.JobsOptions)
        captured.append(options)
        return 0

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(cli, "run_jobs", fake_run_jobs)
        assert cli.main(["jobs", *argv]) == 0
    [options] = captured
    return options


# --- parsing --------------------------------------------------------------------


def test_defaults_are_the_latest_fifty_as_a_table() -> None:
    assert parse([]) == cli.JobsOptions(
        new_in_last_run=False, since=None, limit=50, output="table"
    )


def test_every_option_reaches_the_options() -> None:
    options = parse(
        [
            "--since",
            "2026-11-12T00:00:00Z",
            "--company",
            "acme",
            "--title",
            "Engineer",
            "--location",
            "Mexico",
            "--department",
            "Platform",
            "--limit",
            "7",
            "--format",
            "jsonl",
        ]
    )

    assert options == cli.JobsOptions(
        new_in_last_run=False,
        since=datetime(2026, 11, 12, tzinfo=UTC),
        company="acme",
        title="Engineer",
        location="Mexico",
        department="Platform",
        limit=7,
        output="jsonl",
    )


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("2026-11-12T00:00:00Z", datetime(2026, 11, 12, tzinfo=UTC)),
        ("2026-11-12T05:00:00+05:00", datetime(2026, 11, 12, tzinfo=UTC)),
        ("2026-11-11T18:00:00-06:00", datetime(2026, 11, 12, tzinfo=UTC)),
        (
            "2026-11-12T18:04:11.123456+00:00",
            datetime(2026, 11, 12, 18, 4, 11, 123456, tzinfo=UTC),
        ),
    ],
)
def test_since_is_normalized_to_utc(given: str, expected: datetime) -> None:
    since = parse(["--since", given]).since

    assert since == expected
    assert since is not None
    assert since.utcoffset() == timedelta(0)
    assert since.tzinfo is UTC


def test_new_in_last_run_is_a_flag() -> None:
    assert parse(["--new-in-last-run"]).new_in_last_run is True


@pytest.mark.parametrize("limit", ["1", "1000"])
def test_limit_bounds_are_inclusive(limit: str) -> None:
    assert parse(["--limit", limit]).limit == int(limit)


@pytest.mark.parametrize(
    "argv",
    [
        ["--since", "2026-11-12"],
        ["--since", "2026-11-12T00:00:00"],
        ["--since", "yesterday"],
        ["--new-in-last-run", "--since", "2026-11-12T00:00:00Z"],
        ["--limit", "0"],
        ["--limit", "1001"],
        ["--limit", "-5"],
        ["--limit", "abc"],
        ["--title", ""],
        ["--company", ""],
        ["--title", "a", "--title", "b"],
        ["--limit", "5", "--limit", "5"],
        ["--format", "table", "--format", "jsonl"],
        ["--since", "2026-11-12T00:00:00Z", "--since", "2026-11-13T00:00:00Z"],
        ["--format", "csv"],
        ["--verbose"],
    ],
)
def test_invalid_usage_exits_two_before_anything_runs(
    argv: list[str], engines: Engines
) -> None:
    with pytest.raises(SystemExit) as exited:
        cli.main(["jobs", *argv])

    assert exited.value.code == 2
    assert engines.created == []


def test_version_still_wins_over_the_jobs_command(
    capsys: pytest.CaptureFixture[str], engines: Engines
) -> None:
    assert cli.main(["--version", "jobs"]) == 0
    assert capsys.readouterr().out.startswith("job-radar ")
    assert engines.created == []


def test_jobs_command_runs_the_real_composition_on_the_process_streams(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = cli.main(["jobs", "--format", "jsonl"])

    assert code == 0
    captured = capsys.readouterr()
    assert [
        json.loads(line)["source_job_id"] for line in captured.out.splitlines()
    ] == ["1"]
    assert [json.loads(line)["message"] for line in captured.err.splitlines()] == [
        "listed 1 jobs"
    ]


# --- selection --------------------------------------------------------------------


def test_default_listing_queries_the_whole_history(data: FakeData) -> None:
    output = jobs()

    assert output.code == 0
    assert data.criteria == [JobCriteria(limit=50)]
    assert data.run_lookups == 0
    assert data.unfinished_after == []


def test_filters_and_limit_reach_the_query(data: FakeData) -> None:
    since = datetime(2026, 11, 1, tzinfo=UTC)

    jobs(
        since=since,
        company="acme",
        title="Engineer",
        location="Mexico",
        department="Platform",
        limit=3,
    )

    assert data.criteria == [
        JobCriteria(
            first_seen_since=since,
            company="acme",
            title="Engineer",
            location="Mexico",
            department="Platform",
            limit=3,
        )
    ]


def test_new_in_last_run_correlates_jobs_with_the_runs_start(data: FakeData) -> None:
    jobs(new_in_last_run=True, location="Mexico")

    assert data.criteria == [JobCriteria(first_seen_at=STARTED, location="Mexico")]
    assert data.unfinished_after == [42]


def test_new_in_last_run_without_a_finished_run_lists_nothing(data: FakeData) -> None:
    data.run = None
    data.unfinished = 2

    output = jobs(new_in_last_run=True)

    assert output.code == 0
    assert data.criteria == []
    assert data.unfinished_after == [None]
    assert output.stdout == (
        "No finished crawl run.\n"
        "note: 2 later runs did not finish (status running);"
        " jobs they stored are not included here.\n"
    )
    assert output.records[0]["message"] == "no finished crawl run"
    assert output.records[0]["later_unfinished_runs"] == 2


@pytest.mark.parametrize(
    "status",
    [CrawlRunStatus.FAILED, CrawlRunStatus.SUCCESS, CrawlRunStatus.PARTIAL_SUCCESS],
)
def test_a_run_that_inserted_nothing_says_so(
    data: FakeData, status: CrawlRunStatus
) -> None:
    data.run = finished(status, new=0)
    data.page = page()

    output = jobs(new_in_last_run=True)

    assert output.code == 0
    assert output.stdout == (
        f"crawl run 42 ({status}), started 2026-11-12T18:04:11.123456+00:00,"
        " 0 new jobs recorded\n"
        "No matching jobs.\n"
    )


def test_filters_that_exclude_a_runs_new_jobs_are_distinguishable(
    data: FakeData,
) -> None:
    data.run = finished(CrawlRunStatus.PARTIAL_SUCCESS, new=3)
    data.page = page()

    output = jobs(new_in_last_run=True, title="nothing")

    assert output.stdout.splitlines() == [
        "crawl run 42 (partial_success), started 2026-11-12T18:04:11.123456+00:00,"
        " 3 new jobs recorded",
        "No matching jobs.",
    ]


def test_later_unfinished_runs_are_reported_not_included(data: FakeData) -> None:
    data.run = finished(new=1)
    data.unfinished = 1

    output = jobs(new_in_last_run=True)

    assert output.stdout.splitlines()[:2] == [
        "crawl run 42 (success), started 2026-11-12T18:04:11.123456+00:00,"
        " 1 new job recorded",
        "note: 1 later run did not finish (status running);"
        " jobs they stored are not included here.",
    ]
    [context, listed] = output.records
    assert context == {
        "time": context["time"],
        "level": "INFO",
        "logger": cli.CLI_LOGGER,
        "message": "selected the latest finished crawl run",
        "run_id": 42,
        "status": "success",
        "new": 1,
        "later_unfinished_runs": 1,
        "started_at": "2026-11-12T18:04:11.123456+00:00",
    }
    assert listed["limit"] == 50
    assert listed["more_available"] is False


# --- table ------------------------------------------------------------------------


def table(*jobs_: ListedJob, more: bool = False) -> str:
    return cli.render_jobs_table(cli.JobsResult(page=page(*jobs_, more=more)))


def test_table_is_exact_and_independent_of_the_terminal() -> None:
    text = table(
        job("1", company="Acme", title="Backend Engineer", location="Remote, Mexico"),
        job(
            "2",
            company="Globex",
            title="Data Engineer",
            location=None,
            first_seen_at=STARTED - timedelta(days=1),
        ),
    )

    assert text == (
        "FIRST SEEN (UTC)  COMPANY               TITLE"
        "                                     LOCATION                  URL\n"
        "2026-11-12 18:04  Acme                  Backend Engineer"
        "                          Remote, Mexico            "
        "https://boards.example/acme/jobs/1\n"
        "2026-11-11 18:04  Globex                Data Engineer"
        "                             -                         "
        "https://boards.example/acme/jobs/2\n"
        "2 jobs.\n"
    )


def test_long_cells_are_truncated_but_the_url_never_is() -> None:
    url = "https://boards.example/acme/jobs/" + "x" * 200
    text = table(job(company="C" * 30, title="T" * 60, location="L" * 30, job_url=url))

    row = text.splitlines()[1]
    assert ("C" * 17 + "...") in row
    assert ("T" * 37 + "...") in row
    assert ("L" * 21 + "...") in row
    assert row.endswith(url)


def test_first_seen_is_shown_in_utc() -> None:
    mexico_city = timezone(timedelta(hours=-6))
    text = table(job(first_seen_at=datetime(2026, 11, 12, 6, 30, tzinfo=mexico_city)))

    assert text.splitlines()[1].startswith("2026-11-12 12:30  ")


def test_empty_listing_says_so() -> None:
    assert table() == "No matching jobs.\n"


def test_footer_says_when_more_matched() -> None:
    assert table(job(), more=True).splitlines()[-1] == (
        "1 jobs shown; more match (raise --limit, max 1000)."
    )
    assert table(job()).splitlines()[-1] == "1 job."


def test_display_text_removes_every_unsafe_character() -> None:
    cleaned = cli.display_text(HOSTILE)

    assert cleaned == "Eng [2Jineer 31m evil iso next line zw"
    assert not [char for char in cleaned if unicodedata.category(char) in UNSAFE]


@pytest.mark.parametrize("value", [None, "", " \t\n", "\x1b\u202e"])
def test_missing_or_blank_cells_show_a_dash(value: str | None) -> None:
    assert cli.display_text(value) == "-"


def test_hostile_stored_text_cannot_reach_the_terminal() -> None:
    text = table(
        job(
            company=HOSTILE,
            title=HOSTILE,
            location=HOSTILE,
            job_url="https://x.example/\x1b]8;;evil\x07",
        )
    )

    body = text.replace("\n", "")
    assert not [char for char in body if unicodedata.category(char) in UNSAFE]
    # One line per job: no injected line breaks.
    assert len(text.splitlines()) == 3


# --- JSON Lines -------------------------------------------------------------------


def test_jsonl_has_the_documented_fields_types_and_order() -> None:
    listed = ListedJob(
        source="greenhouse",
        source_job_id="7712345",
        company="Duolingo",
        board_token="duolingo",
        title="Senior Software Engineer",
        location=None,
        department="Engineering",
        job_url="https://boards.example/duolingo/jobs/7712345",
        apply_url=None,
        source_published_at=datetime(
            2026, 11, 10, 9, 2, 33, tzinfo=timezone(timedelta(hours=-6))
        ),
        source_updated_at=None,
        first_seen_at=STARTED,
        last_seen_at=STARTED.replace(microsecond=0),
        is_active=True,
    )

    [line] = cli.render_jobs_jsonl(page(listed)).splitlines()

    assert line == (
        '{"source": "greenhouse", "source_job_id": "7712345", "company": "Duolingo",'
        ' "board_token": "duolingo", "title": "Senior Software Engineer",'
        ' "location": null, "department": "Engineering",'
        ' "job_url": "https://boards.example/duolingo/jobs/7712345",'
        ' "apply_url": null,'
        ' "source_published_at": "2026-11-10T15:02:33.000000+00:00",'
        ' "source_updated_at": null,'
        ' "first_seen_at": "2026-11-12T18:04:11.123456+00:00",'
        ' "last_seen_at": "2026-11-12T18:04:11.000000+00:00",'
        ' "is_active": true}'
    )


def test_jsonl_is_ascii_one_line_per_job_and_round_trips() -> None:
    text = cli.render_jobs_jsonl(
        page(job("1", title=HOSTILE), job("2", title="Ingeniería"))
    )

    assert text.isascii()
    lines = text.splitlines()
    assert len(lines) == 2
    assert text.endswith("\n")
    assert json.loads(lines[0])["title"] == HOSTILE
    assert json.loads(lines[1])["title"] == "Ingeniería"


def test_jsonl_of_nothing_is_empty() -> None:
    assert cli.render_jobs_jsonl(page()) == ""


def test_jsonl_stdout_carries_jobs_only(data: FakeData) -> None:
    data.page = page(job("1"), job("2"), more=True)
    data.unfinished = 3

    output = jobs(new_in_last_run=True, output="jsonl")

    objects = [json.loads(line) for line in output.stdout.splitlines()]
    assert [obj["source_job_id"] for obj in objects] == ["1", "2"]
    assert all(set(obj) == set(cli._job_object(job())) for obj in objects)
    assert [record["message"] for record in output.records] == [
        "selected the latest finished crawl run",
        "listed 2 jobs",
    ]
    assert output.records[1]["more_available"] is True


# --- settings and failures ----------------------------------------------------------


def test_ingestion_settings_cannot_stop_a_read(
    data: FakeData, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # D15: only the database URL is read.
    monkeypatch.setenv("JOB_RADAR_HTTP_TIMEOUT_SECONDS", "-1")
    monkeypatch.setenv("JOB_RADAR_BOARDS_CONFIG_PATH", str(tmp_path / "missing.yml"))

    output = jobs()

    assert output.code == 0
    assert len(data.criteria) == 1


def test_an_unusable_database_url_exits_one_without_revealing_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "JOB_RADAR_DATABASE_URL",
        "postgresql+psycopg://scott:hunter2-secret@localhost:notaport/jobradar",
    )

    output = jobs()

    assert output.code == 1
    assert output.stdout == ""
    [record] = output.records
    assert record["operation"] == "create_engine"
    assert record["error_type"] == "SettingsError"
    assert record["message"] == "jobs failed during create_engine; nothing was queried"
    assert "hunter2" not in json.dumps(record)


def test_a_database_error_exits_one_with_class_and_sqlstate_only(
    data: FakeData, engines: Engines
) -> None:
    driver_error = Exception(f"could not connect to {SECRET_URL}")
    driver_error.sqlstate = "08006"  # type: ignore[attr-defined]
    data.error = OperationalError(
        "SELECT * FROM jobs WHERE title ILIKE %(secret_filter)s",
        {"secret_filter": "%confidential%"},
        driver_error,
    )

    output = jobs(title="confidential")

    assert output.code == 1
    assert output.stdout == ""
    [record] = output.records
    assert record["operation"] == "query_jobs"
    assert record["error_type"] == "PersistenceError"
    assert record["error"] == "OperationalError (SQLSTATE 08006)"
    rendered = json.dumps(record)
    for secret in ("hunter2", "db.internal", "SELECT", "confidential"):
        assert secret not in rendered
    assert engines.disposed == engines.created


@pytest.mark.parametrize("new_in_last_run", [False, True])
def test_the_engine_is_disposed_after_a_query(
    engines: Engines, new_in_last_run: bool
) -> None:
    jobs(new_in_last_run=new_in_last_run)

    assert len(engines.created) == 1
    assert engines.disposed == engines.created


def test_an_unexpected_error_propagates_and_still_releases_the_engine(
    data: FakeData, engines: Engines
) -> None:
    data.error = RuntimeError("bug")

    with pytest.raises(RuntimeError, match="bug"):
        jobs()

    assert engines.disposed == engines.created


@pytest.mark.parametrize("fails", [False, True])
def test_the_session_is_rolled_back_and_closed_never_committed(
    data: FakeData, monkeypatch: pytest.MonkeyPatch, fails: bool
) -> None:
    events: list[str] = []
    if fails:
        data.error = OperationalError("SELECT 1", {}, Exception("down"))

    @contextmanager
    def spy(engine: Engine) -> Iterator[Session]:
        with read_only_session(engine) as session:
            rollback, close = session.rollback, session.close

            def record(name: str, then: Callable[[], None]) -> Callable[[], None]:
                def wrapper() -> None:
                    events.append(name)
                    then()

                return wrapper

            monkeypatch.setattr(session, "commit", record("commit", lambda: None))
            monkeypatch.setattr(session, "rollback", record("rollback", rollback))
            monkeypatch.setattr(session, "close", record("closed", close))
            yield session

    monkeypatch.setattr(cli, "read_only_session", spy)

    assert jobs().code == (1 if fails else 0)
    assert events == ["rollback", "closed"]
