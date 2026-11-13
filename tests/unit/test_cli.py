"""The command line: parsing, wiring, reporting, logging and failures.

No database and no network. The ingestion service is replaced by a fake, so
these tests cover what the CLI itself decides: exit codes, what reaches
stdout and stderr, and that every resource it opened is released. The real
pipeline behind the CLI is exercised by tests/integration/test_cli_acceptance.py.
"""

import io
import json
import logging
import sys
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from sqlalchemy import Engine

from job_radar import __version__, cli
from job_radar.collectors.greenhouse.collector import create_client
from job_radar.config.boards import BoardsConfig
from job_radar.domain.clock import Clock, SystemClock
from job_radar.domain.crawl import CompanyCrawlStatus, CrawlRunStatus, JobCounters
from job_radar.domain.errors import PersistenceError
from job_radar.domain.job import JobSource
from job_radar.ingestion.summary import CompanyCrawlSummary, CrawlRunSummary
from job_radar.persistence.session import create_database_engine
from tests.support.clock import FixedClock

OBSERVED = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
FINISHED = datetime(2026, 9, 15, 12, 0, 5, tzinfo=UTC)

BOARDS_YAML = """\
boards:
  - source: greenhouse
    board_token: acme
    display_name: Acme
    enabled: true
"""


# --- builders -------------------------------------------------------------------


def company(
    token: str,
    status: CompanyCrawlStatus,
    counters: JobCounters,
    *,
    error_type: str | None = None,
    error_message: str | None = None,
) -> CompanyCrawlSummary:
    return CompanyCrawlSummary(
        company_id=1,
        source="greenhouse",
        board_token=token,
        display_name=token.title(),
        status=status,
        started_at=OBSERVED,
        finished_at=FINISHED,
        counters=counters,
        error_type=error_type,
        error_message=error_message,
    )


def summary(status: CrawlRunStatus, *companies: CompanyCrawlSummary) -> CrawlRunSummary:
    totals = JobCounters()
    for entry in companies:
        totals = totals + entry.counters
    return CrawlRunSummary(
        run_id=42,
        status=status,
        observed_at=OBSERVED,
        finished_at=FINISHED,
        companies=companies,
        totals=totals,
    )


SUCCESS = company(
    "acme", CompanyCrawlStatus.SUCCESS, JobCounters(fetched=3, new=2, existing=1)
)
FAILED = company(
    "broken",
    CompanyCrawlStatus.FAILED,
    JobCounters(),
    error_type="CollectorError",
    error_message="HTTP 500",
)


# --- fakes ----------------------------------------------------------------------


@dataclass
class Wiring:
    """What the CLI built, and what it released."""

    outcome: Callable[[], CrawlRunSummary] = field(
        default=lambda: summary(CrawlRunStatus.SUCCESS)
    )
    services: list["FakeService"] = field(default_factory=list)
    engines: list[Engine] = field(default_factory=list)
    disposed: list[Engine] = field(default_factory=list)
    clients: list[httpx.Client] = field(default_factory=list)


class FakeService:
    """Stands in for IngestionService; its run() does what the test says."""

    wiring: Wiring

    def __init__(
        self,
        *,
        store: object,
        collectors: Mapping[JobSource, object],
        normalizers: Mapping[JobSource, object],
        clock: Clock,
    ) -> None:
        self.store = store
        self.collectors = collectors
        self.normalizers = normalizers
        self.clock = clock
        self.boards: BoardsConfig | None = None
        self.wiring.services.append(self)

    def run(self, boards: BoardsConfig) -> CrawlRunSummary:
        self.boards = boards
        return self.wiring.outcome()


@pytest.fixture
def boards_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "boards.yml"
    path.write_text(BOARDS_YAML, encoding="utf-8")
    monkeypatch.setenv("JOB_RADAR_BOARDS_CONFIG_PATH", str(path))
    return path


@pytest.fixture
def wiring(monkeypatch: pytest.MonkeyPatch) -> Wiring:
    """Replace the service, and record every engine and client the CLI makes.

    The engine is real but never connects: nothing calls the fake store.
    """
    recorded = Wiring()
    real_engine = create_database_engine
    real_client = create_client

    def engine_factory(url: str) -> Engine:
        engine = real_engine(url)
        recorded.engines.append(engine)
        original = engine.dispose

        def dispose(close: bool = True) -> None:
            recorded.disposed.append(engine)
            original(close)

        monkeypatch.setattr(engine, "dispose", dispose)
        return engine

    def client_factory(
        *, timeout_seconds: float, transport: httpx.BaseTransport | None = None
    ) -> httpx.Client:
        client = real_client(timeout_seconds=timeout_seconds, transport=transport)
        recorded.clients.append(client)
        return client

    monkeypatch.setattr(cli, "create_database_engine", engine_factory)
    monkeypatch.setattr(cli, "create_client", client_factory)
    monkeypatch.setattr(cli, "IngestionService", FakeService)
    monkeypatch.setattr(FakeService, "wiring", recorded, raising=False)
    return recorded


def returns(result: CrawlRunSummary) -> Callable[[], CrawlRunSummary]:
    return lambda: result


def raises(error: Exception) -> Callable[[], CrawlRunSummary]:
    def outcome() -> CrawlRunSummary:
        raise error

    return outcome


@dataclass
class Output:
    code: int
    stdout: str
    records: list[dict[str, object]]


def ingest(clock: Clock | None = None) -> Output:
    stdout, stderr = io.StringIO(), io.StringIO()
    code = cli.run_ingest(stdout=stdout, stderr=stderr, clock=clock)
    records = [json.loads(line) for line in stderr.getvalue().splitlines()]
    return Output(code, stdout.getvalue(), records)


@pytest.fixture(autouse=True)
def logging_is_restored() -> Iterator[None]:
    """Every test leaves the job_radar logger exactly as it found it."""
    logger = logging.getLogger(cli.APP_LOGGER)
    handlers, level = list(logger.handlers), logger.level
    yield
    assert logger.handlers == handlers
    assert logger.level == level


# --- parsing --------------------------------------------------------------------


def test_no_arguments_prints_the_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main([]) == 0

    assert capsys.readouterr().out == f"job-radar {__version__}\n"


def test_version_flag_prints_the_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--version"]) == 0

    assert capsys.readouterr().out == f"job-radar {__version__}\n"


def test_version_flag_wins_over_a_command(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "run_ingest", raises(AssertionError("ran ingest")))

    assert cli.main(["--version", "ingest"]) == 0
    assert capsys.readouterr().out == f"job-radar {__version__}\n"


@pytest.mark.parametrize(
    "argv", [["crawl"], ["--verbose"], ["ingest", "--config", "x.yml"]]
)
def test_unknown_commands_and_arguments_are_usage_errors(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as exited:
        cli.main(argv)

    assert exited.value.code == 2


def test_ingest_command_delegates_to_run_ingest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    def fake_run_ingest(**kwargs: object) -> int:
        calls.append(kwargs)
        return 7

    monkeypatch.setattr(cli, "run_ingest", fake_run_ingest)

    assert cli.main(["ingest"]) == 7
    assert len(calls) == 1


@pytest.mark.usefixtures("boards_file")
def test_ingest_command_runs_the_real_composition_on_the_process_streams(
    wiring: Wiring, capsys: pytest.CaptureFixture[str]
) -> None:
    # main(["ingest"]) through the real run_ingest: production clock and HTTP
    # transport, the summary on sys.stdout, every resource released.
    result = summary(CrawlRunStatus.PARTIAL_SUCCESS, SUCCESS, FAILED)
    wiring.outcome = returns(result)

    code = cli.main(["ingest"])

    assert code == 0
    captured = capsys.readouterr()
    assert captured.out == cli.render_summary(result)
    assert captured.err == ""
    [service] = wiring.services
    assert isinstance(service.clock, SystemClock)
    assert wiring.disposed == wiring.engines
    assert len(wiring.engines) == 1
    assert [client.is_closed for client in wiring.clients] == [True]


def test_ingest_command_reports_a_failure_on_sys_stderr_and_exits_one(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    wiring: Wiring,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("JOB_RADAR_BOARDS_CONFIG_PATH", str(tmp_path / "missing.yml"))

    code = cli.main(["ingest"])

    assert code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    [record] = [json.loads(line) for line in captured.err.splitlines()]
    assert record["logger"] == cli.CLI_LOGGER
    assert record["operation"] == "load_boards_config"
    assert wiring.engines == []
    assert wiring.clients == []


# --- a completed run --------------------------------------------------------------


@pytest.mark.parametrize(
    "result",
    [
        summary(CrawlRunStatus.SUCCESS, SUCCESS),
        summary(CrawlRunStatus.PARTIAL_SUCCESS, SUCCESS, FAILED),
        summary(CrawlRunStatus.FAILED, FAILED),
        summary(CrawlRunStatus.SUCCESS),
    ],
    ids=["success", "partial_success", "failed", "no-companies"],
)
@pytest.mark.usefixtures("boards_file")
def test_every_completed_run_exits_zero(
    wiring: Wiring, result: CrawlRunSummary
) -> None:
    # SPEC, "Run reporting": the run's status never sets the exit code.
    wiring.outcome = returns(result)

    output = ingest()

    assert output.code == 0
    assert output.stdout == cli.render_summary(result)


@pytest.mark.usefixtures("boards_file")
def test_the_service_is_wired_with_greenhouse_and_the_configured_boards(
    wiring: Wiring,
) -> None:
    wiring.outcome = returns(summary(CrawlRunStatus.SUCCESS))

    ingest()

    [service] = wiring.services
    assert set(service.collectors) == {JobSource.GREENHOUSE}
    assert set(service.normalizers) == {JobSource.GREENHOUSE}
    assert isinstance(service.clock, SystemClock)
    assert service.boards is not None
    assert [board.board_token for board in service.boards.boards] == ["acme"]


@pytest.mark.usefixtures("boards_file")
def test_an_injected_clock_reaches_the_service(wiring: Wiring) -> None:
    wiring.outcome = returns(summary(CrawlRunStatus.SUCCESS))
    clock = FixedClock(OBSERVED)

    ingest(clock)

    assert wiring.services[0].clock is clock


@pytest.mark.usefixtures("boards_file")
def test_a_completed_run_releases_the_engine_and_the_client(wiring: Wiring) -> None:
    wiring.outcome = returns(summary(CrawlRunStatus.SUCCESS))

    ingest()

    assert wiring.disposed == wiring.engines
    assert len(wiring.engines) == 1
    assert [client.is_closed for client in wiring.clients] == [True]


# --- run summary ----------------------------------------------------------------


def test_summary_lists_status_companies_and_totals() -> None:
    text = cli.render_summary(summary(CrawlRunStatus.PARTIAL_SUCCESS, SUCCESS, FAILED))

    assert text == (
        "crawl run 42: partial_success\n"
        "observed at: 2026-09-15T12:00:00+00:00\n"
        "finished at: 2026-09-15T12:00:05+00:00\n"
        "companies: 2 total, 1 succeeded, 1 failed\n"
        "  success greenhouse/acme (Acme):"
        " fetched=3 new=2 existing=1 updated=0 invalid=0\n"
        "  failed greenhouse/broken (Broken):"
        " fetched=0 new=0 existing=0 updated=0 invalid=0;"
        " CollectorError: HTTP 500\n"
        "totals: fetched=3 new=2 existing=1 updated=0 invalid=0\n"
    )


def test_summary_of_a_run_without_companies() -> None:
    text = cli.render_summary(summary(CrawlRunStatus.SUCCESS))

    assert text.splitlines()[0] == "crawl run 42: success"
    assert "companies: 0 total, 0 succeeded, 0 failed" in text
    assert text.endswith("totals: fetched=0 new=0 existing=0 updated=0 invalid=0\n")


# --- configuration failures ---------------------------------------------------------


def test_a_missing_board_file_fails_before_any_engine_or_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, wiring: Wiring
) -> None:
    monkeypatch.setenv("JOB_RADAR_BOARDS_CONFIG_PATH", str(tmp_path / "missing.yml"))

    output = ingest()

    assert output.code == 1
    assert output.stdout == ""
    assert wiring.engines == []
    assert wiring.clients == []
    [record] = output.records
    assert record["level"] == "ERROR"
    assert record["logger"] == cli.CLI_LOGGER
    assert record["operation"] == "load_boards_config"
    assert record["error_type"] == "BoardConfigError"
    assert "missing.yml" in str(record["error"])


@pytest.mark.parametrize(
    "content",
    [
        "boards: [",
        "- just a list",
        "boards:\n  - source: lever\n    board_token: x\n    display_name: X\n",
    ],
    ids=["yaml", "not-a-mapping", "unsupported-source"],
)
def test_an_invalid_board_file_fails_before_any_engine_or_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, wiring: Wiring, content: str
) -> None:
    path = tmp_path / "boards.yml"
    path.write_text(content, encoding="utf-8")
    monkeypatch.setenv("JOB_RADAR_BOARDS_CONFIG_PATH", str(path))

    output = ingest()

    assert output.code == 1
    assert wiring.engines == []
    assert wiring.clients == []
    assert [r["operation"] for r in output.records] == ["load_boards_config"]


@pytest.mark.usefixtures("boards_file")
def test_invalid_settings_are_reported_without_their_values(
    monkeypatch: pytest.MonkeyPatch, wiring: Wiring
) -> None:
    monkeypatch.setenv("JOB_RADAR_HTTP_TIMEOUT_SECONDS", "-31337")

    output = ingest()

    assert output.code == 1
    assert wiring.engines == []
    [record] = output.records
    assert record["operation"] == "load_settings"
    assert record["error_type"] == "SettingsError"
    assert "http_timeout_seconds" in str(record["error"])
    assert "31337" not in json.dumps(output.records)


@pytest.mark.parametrize(
    "url",
    [
        "nosuchdialect://scott:hunter2-secret@localhost/jobradar",
        "postgresql+psycopg://scott:hunter2-secret@localhost:notaport/jobradar",
    ],
    ids=["unknown-dialect", "bad-port"],
)
@pytest.mark.usefixtures("boards_file")
def test_an_unusable_database_url_never_reveals_it(
    monkeypatch: pytest.MonkeyPatch, wiring: Wiring, url: str
) -> None:
    monkeypatch.setenv("JOB_RADAR_DATABASE_URL", url)

    output = ingest()

    assert output.code == 1
    assert wiring.clients == []
    [record] = output.records
    assert record["operation"] == "create_engine"
    assert record["error_type"] == "SettingsError"
    rendered = json.dumps(output.records)
    assert "hunter2" not in rendered
    assert "scott" not in rendered


# --- persistence failures and crashes -------------------------------------------------


@pytest.mark.parametrize(
    ("operation", "consequence"),
    [
        ("reconcile_config", "no crawl run was started"),
        ("start_run_commit", "the crawl run may remain 'running'"),
        ("finish_run", "the crawl run may remain 'running'"),
    ],
)
@pytest.mark.usefixtures("boards_file")
def test_a_persistence_failure_exits_one_and_releases_resources(
    wiring: Wiring, operation: str, consequence: str
) -> None:
    error = PersistenceError(
        operation=operation, category="OperationalError", sqlstate="08006"
    )
    wiring.outcome = raises(error)

    output = ingest()

    assert output.code == 1
    assert output.stdout == ""
    [record] = output.records
    assert record["operation"] == operation
    assert record["error_type"] == "PersistenceError"
    assert record["error"] == "OperationalError (SQLSTATE 08006)"
    assert consequence in str(record["message"])
    assert wiring.disposed == wiring.engines
    assert [client.is_closed for client in wiring.clients] == [True]


@pytest.mark.parametrize(
    "error", [RuntimeError("bug"), ValueError("no collector wired")]
)
@pytest.mark.usefixtures("boards_file")
def test_a_programming_error_propagates_and_still_releases_resources(
    wiring: Wiring, error: Exception
) -> None:
    # A crash is not an expected outcome: it must not be turned into exit 1.
    wiring.outcome = raises(error)

    with pytest.raises(type(error)):
        ingest()

    assert wiring.disposed == wiring.engines
    assert [client.is_closed for client in wiring.clients] == [True]


# --- structured logging ---------------------------------------------------------------


def format_record(**extra: object) -> dict[str, object]:
    record = logging.LogRecord(
        "job_radar.ingestion", logging.ERROR, __file__, 1, "msg %s", ("arg",), None
    )
    record.__dict__.update(extra)
    parsed: dict[str, object] = json.loads(cli.JsonLinesFormatter().format(record))
    return parsed


def test_log_lines_carry_the_required_failure_fields() -> None:
    entry = format_record(
        source="greenhouse",
        board_token="acme",
        display_name="Acme",
        operation="fetch",
        error_type="CollectorError",
        error="HTTP 500",
        fetched=0,
    )

    assert entry["level"] == "ERROR"
    assert entry["logger"] == "job_radar.ingestion"
    assert entry["message"] == "msg arg"
    assert {k: entry[k] for k in ("source", "board_token", "display_name")} == {
        "source": "greenhouse",
        "board_token": "acme",
        "display_name": "Acme",
    }
    assert entry["operation"] == "fetch"
    assert entry["error"] == "HTTP 500"
    assert entry["fetched"] == 0
    assert str(entry["time"]).endswith("+00:00")


def test_log_lines_drop_fields_outside_the_allowlist() -> None:
    entry = format_record(database_url="postgresql://u:secret@h/db", password="x")

    assert "database_url" not in entry
    assert "password" not in entry
    assert "secret" not in json.dumps(entry)


def test_log_values_that_are_not_scalars_are_named_not_serialized() -> None:
    entry = format_record(error={"sql": "SELECT secret"}, run_id=1.5)

    assert entry["error"] == "<dict>"
    assert entry["run_id"] == "<float>"


def test_log_lines_never_include_a_traceback() -> None:
    try:
        raise RuntimeError("INSERT INTO jobs VALUES ('row content')")
    except RuntimeError:
        record = logging.LogRecord(
            "job_radar", logging.ERROR, __file__, 1, "failed", (), sys.exc_info()
        )

    line = cli.JsonLinesFormatter().format(record)

    assert "row content" not in line
    assert "Traceback" not in line


def test_a_log_line_stays_one_line_and_bounded() -> None:
    entry_line = cli.JsonLinesFormatter().format(
        logging.LogRecord(
            "job_radar", logging.INFO, __file__, 1, "a\nb" + "x" * 5000, (), None
        )
    )

    assert "\n" not in entry_line
    message = json.loads(entry_line)["message"]
    assert len(message) == cli.MAX_LOG_TEXT
    assert message.endswith("...")


def test_structured_logging_emits_info_records_and_then_restores_the_logger() -> None:
    stream = io.StringIO()
    logger = logging.getLogger("job_radar.ingestion")

    with cli.structured_logging(stream):
        logger.info("run finished", extra={"status": "success"})
    logger.info("after the block")

    [line] = stream.getvalue().splitlines()
    assert json.loads(line)["status"] == "success"


def test_structured_logging_is_not_installed_twice() -> None:
    stream = io.StringIO()
    app = logging.getLogger(cli.APP_LOGGER)
    before = len(app.handlers)

    with cli.structured_logging(stream), cli.structured_logging(io.StringIO()):
        assert len(app.handlers) == before + 1
        logging.getLogger("job_radar.ingestion").warning("once")

    assert len(stream.getvalue().splitlines()) == 1
    assert len(app.handlers) == before


def test_structured_logging_restores_the_logger_after_an_error() -> None:
    with pytest.raises(RuntimeError), cli.structured_logging(io.StringIO()):
        raise RuntimeError("boom")
    # The autouse fixture asserts the handlers and level are back.


def test_third_party_loggers_are_not_captured() -> None:
    stream = io.StringIO()

    with cli.structured_logging(stream):
        logging.getLogger("sqlalchemy.engine").warning("SELECT secret")

    assert stream.getvalue() == ""
