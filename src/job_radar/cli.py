"""Command-line entry point (SPEC, "Command-line interface").

``job-radar`` and ``job-radar --version`` print the installed version.
``job-radar ingest`` runs one crawl over every enabled board.
``job-radar jobs`` lists stored jobs, read-only (SPEC, "Job listing and
query").

This module is the composition root. It is the only place where the
Greenhouse collector and normalizer, the database engine and the HTTP client
are wired together, and it owns their lifecycle (ARCHITECTURE, "Transaction
ownership"). The ingestion service owns the ingestion transactions; ``jobs``
owns its single read-only transaction.
"""

import argparse
import json
import logging
import sys
import unicodedata
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final, Literal, TextIO

import httpx
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from job_radar import __version__
from job_radar.collectors.greenhouse.collector import (
    GreenhouseCollector,
    create_client,
)
from job_radar.collectors.greenhouse.normalizer import GreenhouseNormalizer
from job_radar.config.boards import BoardConfigError, load_boards_config
from job_radar.domain.clock import Clock, SystemClock
from job_radar.domain.crawl import JobCounters
from job_radar.domain.errors import PersistenceError
from job_radar.domain.job import JobSource
from job_radar.ingestion.service import IngestionService
from job_radar.ingestion.summary import CrawlRunSummary
from job_radar.persistence.ingestion_store import (
    SqlAlchemyStoreFactory,
    to_persistence_error,
)
from job_radar.persistence.job_query import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    FinishedRun,
    JobCriteria,
    JobPage,
    JobQuery,
    ListedJob,
)
from job_radar.persistence.session import (
    create_database_engine,
    create_read_only_engine,
    create_session_factory,
    read_only_session,
)
from job_radar.settings import DatabaseSettings, Settings

#: The run completed, whatever its status (SPEC, "Run reporting"), or the
#: query ran, whatever it selected.
EXIT_OK: Final = 0
#: A documented configuration or persistence failure stopped the command.
EXIT_FAILURE: Final = 1

#: Every Job Radar logger is a child of this one.
APP_LOGGER: Final = "job_radar"
CLI_LOGGER: Final = "job_radar.cli"

#: The only ``extra`` fields a log line carries. Everything else attached to a
#: record is dropped, so a field added later is not serialized by accident.
LOG_FIELDS: Final = (
    "source",
    "board_token",
    "display_name",
    "company_id",
    "operation",
    "source_job_id",
    "outcome",
    "persisted_company_id",
    "incoming_company_id",
    "error_type",
    "error",
    "run_id",
    "status",
    "companies_total",
    "companies_succeeded",
    "companies_failed",
    "fetched",
    "new",
    "existing",
    "updated",
    "invalid",
    "later_unfinished_runs",
    "started_at",
    "limit",
    "more_available",
    "deactivated",
    "deactivation_skipped",
    "absent",
    "previously_active",
)
#: Backstop length for any text value in a log line.
MAX_LOG_TEXT: Final = 1000


# --- entry point ----------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command line. Returns the process exit code."""
    args = _parser().parse_args(argv)
    if args.command == "ingest" and not args.version:
        return run_ingest(stdout=sys.stdout, stderr=sys.stderr)
    if args.command == "jobs" and not args.version:
        return run_jobs(
            stdout=sys.stdout, stderr=sys.stderr, options=JobsOptions.from_args(args)
        )
    sys.stdout.write(f"job-radar {__version__}\n")
    return EXIT_OK


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="job-radar",
        description="Job Radar. Without a command, prints the version.",
    )
    parser.add_argument(
        "--version", action="store_true", help="print the version and exit"
    )
    commands = parser.add_subparsers(dest="command", metavar="COMMAND")
    commands.add_parser(
        "ingest",
        help="crawl every enabled board once",
        description=(
            "Crawl every enabled board in the board configuration once and"
            " record the run. Settings come from JOB_RADAR_* environment"
            " variables or .env."
        ),
    )
    _add_jobs_parser(commands)
    return parser


def _add_jobs_parser(
    commands: "argparse._SubParsersAction[argparse.ArgumentParser]",
) -> None:
    jobs = commands.add_parser(
        "jobs",
        help="list stored jobs (read-only)",
        description=(
            "List stored active jobs, most recently discovered first. Read-only:"
            " no HTTP request, no ingestion, no write. Only JOB_RADAR_DATABASE_URL"
            " is read from the environment or .env."
        ),
        epilog=(
            "Text filters are literal, case-insensitive substrings; accents are"
            " significant. Filters combine with AND."
        ),
    )
    when = jobs.add_mutually_exclusive_group()
    when.add_argument(
        "--new-in-last-run",
        action="store_true",
        help="only jobs first stored by the latest finished crawl run",
    )
    when.add_argument(
        "--since",
        type=_timestamp,
        action=_Once,
        metavar="TIMESTAMP",
        help=(
            "only jobs first stored at or after TIMESTAMP"
            " (ISO 8601 with offset, e.g. 2026-11-12T00:00:00Z)"
        ),
    )
    for name, what in (
        ("company", "company display name or board token"),
        ("title", "title"),
        ("location", "location"),
        ("department", "department"),
    ):
        jobs.add_argument(
            f"--{name}",
            type=_filter_text,
            action=_Once,
            metavar="TEXT",
            help=f"{what} contains TEXT",
        )
    jobs.add_argument(
        "--limit",
        type=_limit,
        action=_Once,
        default=DEFAULT_LIMIT,
        metavar="N",
        help=f"at most N jobs, 1-{MAX_LIMIT} (default: {DEFAULT_LIMIT})",
    )
    jobs.add_argument(
        "--format",
        choices=("table", "jsonl"),
        action=_Once,
        default="table",
        help="table (default) or jsonl",
    )


class _Once(argparse.Action):
    """Store a value, rejecting the option if it is given twice.

    argparse would otherwise keep the last value silently.
    """

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: object,
        option_string: str | None = None,
    ) -> None:
        given: set[str] = getattr(namespace, "_given_once", set())
        if self.dest in given:
            parser.error(f"{option_string} given more than once")
        given.add(self.dest)
        namespace._given_once = given
        setattr(namespace, self.dest, values)


def _timestamp(value: str) -> datetime:
    """An ISO 8601 timestamp with a UTC offset, normalized to UTC."""
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        msg = f"not an ISO 8601 timestamp: {value!r}"
        raise argparse.ArgumentTypeError(msg) from None
    if parsed.utcoffset() is None:
        msg = f"needs a UTC offset or Z, e.g. 2026-11-12T00:00:00Z: {value!r}"
        raise argparse.ArgumentTypeError(msg)
    return parsed.astimezone(UTC)


def _limit(value: str) -> int:
    try:
        limit = int(value)
    except ValueError:
        msg = f"not an integer: {value!r}"
        raise argparse.ArgumentTypeError(msg) from None
    if not 1 <= limit <= MAX_LIMIT:
        msg = f"must be between 1 and {MAX_LIMIT}: {limit}"
        raise argparse.ArgumentTypeError(msg)
    return limit


def _filter_text(value: str) -> str:
    if not value:
        msg = "must not be empty"
        raise argparse.ArgumentTypeError(msg)
    return value


# --- ingest ---------------------------------------------------------------------


def run_ingest(
    *,
    stdout: TextIO,
    stderr: TextIO,
    transport: httpx.BaseTransport | None = None,
    clock: Clock | None = None,
) -> int:
    """Run one ingestion and report it. Returns the process exit code.

    ``transport`` and ``clock`` exist for tests: ``None`` means the network
    and the system clock. The summary goes to ``stdout``; log records,
    including the diagnostics of a failure, go to ``stderr`` as JSON Lines.

    Exit code 0 whenever the run completes, whether its status is
    ``success``, ``partial_success`` or ``failed``. A documented settings,
    configuration or persistence failure exits 1. Any other exception is a
    programming error and propagates.
    """
    with structured_logging(stderr):
        return _ingest(stdout, transport=transport, clock=clock)


def _ingest(
    stdout: TextIO, *, transport: httpx.BaseTransport | None, clock: Clock | None
) -> int:
    log = logging.getLogger(CLI_LOGGER)

    try:
        settings = Settings()
    except ValidationError as exc:
        return _fail(
            log,
            operation="load_settings",
            error_type="SettingsError",
            error=_settings_problem(exc),
            consequence="nothing was crawled",
        )

    # Before any engine or HTTP client exists: a bad file costs nothing.
    try:
        boards = load_boards_config(settings.boards_config_path)
    except BoardConfigError as exc:
        return _fail(
            log,
            operation="load_boards_config",
            error_type=type(exc).__name__,
            error=str(exc),
            consequence="nothing was crawled",
        )

    try:
        engine = create_database_engine(settings.database_url)
    except (SQLAlchemyError, ValueError) as exc:
        # An unparsable URL (SQLAlchemy's ArgumentError, or ValueError for a
        # bad port). The exception's text can quote the URL, password
        # included, so only its class name is reported.
        return _fail(
            log,
            operation="create_engine",
            error_type="SettingsError",
            error=f"invalid settings: database_url: unusable ({type(exc).__name__})",
            consequence="nothing was crawled",
        )

    try:
        with create_client(
            timeout_seconds=settings.http_timeout_seconds, transport=transport
        ) as client:
            service = IngestionService(
                store=SqlAlchemyStoreFactory(create_session_factory(engine)),
                collectors={JobSource.GREENHOUSE: GreenhouseCollector(client)},
                normalizers={JobSource.GREENHOUSE: GreenhouseNormalizer()},
                clock=clock if clock is not None else SystemClock(),
            )
            summary = service.run(boards)
    except PersistenceError as exc:
        return _fail(
            log,
            operation=exc.operation,
            error_type=type(exc).__name__,
            error=exc.message,
            consequence=(
                "no crawl run was started"
                if exc.operation.startswith("reconcile_config")
                else "the crawl run may remain 'running'"
            ),
        )
    finally:
        engine.dispose()

    stdout.write(render_summary(summary))
    return EXIT_OK


def _fail(
    log: logging.Logger,
    *,
    operation: str,
    error_type: str,
    error: str,
    consequence: str,
    command: str = "ingest",
) -> int:
    log.error(
        "%s failed during %s; %s",
        command,
        operation,
        consequence,
        extra={"operation": operation, "error_type": error_type, "error": error},
    )
    return EXIT_FAILURE


def _settings_problem(exc: ValidationError) -> str:
    """Which settings are invalid and why, never their values.

    A value can be a secret (the database URL carries a password), so input
    values and pydantic's own messages, which can quote them, are left out.
    """
    problems = [
        f"{'.'.join(str(part) for part in error['loc']) or 'settings'}: {error['type']}"
        for error in exc.errors(
            include_url=False, include_context=False, include_input=False
        )
    ]
    return "invalid settings: " + "; ".join(problems)


# --- run summary ----------------------------------------------------------------


def render_summary(summary: CrawlRunSummary) -> str:
    """The run as stdout reports it: deterministic, one company per line."""
    lines = [
        f"crawl run {summary.run_id}: {summary.status}",
        f"observed at: {summary.observed_at.isoformat()}",
        f"finished at: {summary.finished_at.isoformat()}",
        (
            f"companies: {len(summary.companies)} total,"
            f" {summary.companies_succeeded} succeeded,"
            f" {summary.companies_failed} failed"
        ),
    ]
    for company in summary.companies:
        line = (
            f"  {company.status} {company.source}/{company.board_token}"
            f" ({company.display_name}): {_counters(company.counters)}"
        )
        if company.error_type is not None:
            line += f"; {company.error_type}: {company.error_message}"
        lines.append(line)
    lines.append(f"totals: {_counters(summary.totals)}")
    return "\n".join(lines) + "\n"


def _counters(counters: JobCounters) -> str:
    return (
        f"fetched={counters.fetched} new={counters.new}"
        f" existing={counters.existing} updated={counters.updated}"
        f" invalid={counters.invalid} deactivated={counters.deactivated}"
    )


# --- jobs -----------------------------------------------------------------------

#: Table columns before the URL, with their widths. The URL is last and never
#: truncated, so it stays usable.
TABLE_COLUMNS: Final = (
    ("FIRST SEEN (UTC)", 16),
    ("COMPANY", 20),
    ("TITLE", 40),
    ("LOCATION", 24),
)
#: Unicode categories never written to a table cell: controls (C0, DEL and
#: C1, which includes the one-byte CSI U+009B), format characters (bidi
#: embeddings, overrides and isolates, direction marks, zero-width
#: characters) and the line and paragraph separators.
UNSAFE_CATEGORIES: Final = frozenset({"Cc", "Cf", "Cs", "Zl", "Zp"})
TRUNCATION_MARK: Final = "..."

OutputFormat = Literal["table", "jsonl"]


@dataclass(frozen=True)
class JobsOptions:
    """What ``job-radar jobs`` was asked for, already validated by argparse."""

    new_in_last_run: bool = False
    since: datetime | None = None
    company: str | None = None
    title: str | None = None
    location: str | None = None
    department: str | None = None
    limit: int = DEFAULT_LIMIT
    output: OutputFormat = "table"

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> "JobsOptions":
        return cls(
            new_in_last_run=args.new_in_last_run,
            since=args.since,
            company=args.company,
            title=args.title,
            location=args.location,
            department=args.department,
            limit=args.limit,
            output=args.format,
        )

    def criteria(self, *, first_seen_at: datetime | None = None) -> JobCriteria:
        return JobCriteria(
            first_seen_since=self.since,
            first_seen_at=first_seen_at,
            company=self.company,
            title=self.title,
            location=self.location,
            department=self.department,
            limit=self.limit,
        )


@dataclass(frozen=True)
class JobsResult:
    """What the query found, and the run context when one was asked for."""

    page: JobPage
    new_in_last_run: bool = False
    run: FinishedRun | None = None
    later_unfinished_runs: int = 0


def run_jobs(*, stdout: TextIO, stderr: TextIO, options: JobsOptions) -> int:
    """List stored jobs. Returns the process exit code.

    Results go to ``stdout``: a table, or one JSON object per job and nothing
    else. Context and diagnostics go to ``stderr`` as JSON Lines log records.
    Exit 0 whenever the query ran, including when it selected nothing; 1 for
    invalid settings, an unusable database URL or a database error. Any other
    exception propagates.
    """
    with structured_logging(stderr):
        return _jobs(stdout, options)


def _jobs(stdout: TextIO, options: JobsOptions) -> int:
    log = logging.getLogger(CLI_LOGGER)

    # Only the database URL: an invalid ingestion setting cannot stop a read.
    try:
        settings = DatabaseSettings()
    except ValidationError as exc:
        return _fail(
            log,
            command="jobs",
            operation="load_settings",
            error_type="SettingsError",
            error=_settings_problem(exc),
            consequence="nothing was queried",
        )

    try:
        engine = create_read_only_engine(settings.database_url)
    except (SQLAlchemyError, ValueError) as exc:
        # As for ingest: the exception's text can quote the URL's password.
        return _fail(
            log,
            command="jobs",
            operation="create_engine",
            error_type="SettingsError",
            error=f"invalid settings: database_url: unusable ({type(exc).__name__})",
            consequence="nothing was queried",
        )

    try:
        with read_only_session(engine) as session:
            result = _query(JobQuery(session), options)
    except SQLAlchemyError as exc:
        # Class name and SQLSTATE only: never SQL, parameters or driver text.
        error = to_persistence_error(exc, operation="query_jobs")
        return _fail(
            log,
            command="jobs",
            operation=error.operation,
            error_type=type(error).__name__,
            error=error.message,
            consequence="nothing was listed",
        )
    finally:
        engine.dispose()

    _log_context(log, result, limit=options.limit)
    if options.output == "jsonl":
        stdout.write(render_jobs_jsonl(result.page))
    else:
        stdout.write(render_jobs_table(result))
    return EXIT_OK


def _query(query: JobQuery, options: JobsOptions) -> JobsResult:
    if not options.new_in_last_run:
        return JobsResult(page=query.list(options.criteria()))
    run = query.latest_finished_run()
    later = query.unfinished_runs_after(run.run_id if run is not None else None)
    if run is None:
        return JobsResult(
            page=JobPage(jobs=(), more_available=False),
            new_in_last_run=True,
            later_unfinished_runs=later,
        )
    # Timestamp correlation: a job belongs to the run whose started_at equals
    # its first_seen_at. Not a recorded link (SPEC, "Job listing and query").
    page = query.list(options.criteria(first_seen_at=run.started_at))
    return JobsResult(
        page=page, new_in_last_run=True, run=run, later_unfinished_runs=later
    )


def _log_context(log: logging.Logger, result: JobsResult, *, limit: int) -> None:
    if result.new_in_last_run:
        if result.run is None:
            log.info(
                "no finished crawl run",
                extra={"later_unfinished_runs": result.later_unfinished_runs},
            )
        else:
            log.info(
                "selected the latest finished crawl run",
                extra={
                    "run_id": result.run.run_id,
                    "status": str(result.run.status),
                    "started_at": _iso(result.run.started_at),
                    "new": result.run.jobs_new,
                    "later_unfinished_runs": result.later_unfinished_runs,
                },
            )
    log.info(
        "listed %d jobs",
        len(result.page.jobs),
        extra={"limit": limit, "more_available": result.page.more_available},
    )


def render_jobs_table(result: JobsResult) -> str:
    """Plain text for a person: no colour, no terminal detection.

    Every database-sourced value is sanitized (``display_text``), so stored
    text cannot move the cursor, clear the screen or reorder the line.
    """
    lines: list[str] = []
    if result.new_in_last_run:
        if result.run is None:
            lines.append("No finished crawl run.")
        else:
            run = result.run
            lines.append(
                f"crawl run {run.run_id} ({run.status}),"
                f" started {_iso(run.started_at)},"
                f" {_count(run.jobs_new, 'new job')} recorded"
            )
        if result.later_unfinished_runs:
            lines.append(
                f"note: {_count(result.later_unfinished_runs, 'later run')} did not"
                " finish (status running); jobs they stored are not included here."
            )
        if result.run is None:
            return "\n".join(lines) + "\n"

    jobs = result.page.jobs
    if not jobs:
        lines.append("No matching jobs.")
        return "\n".join(lines) + "\n"

    lines.append(_row([title for title, _ in TABLE_COLUMNS], "URL"))
    lines.extend(
        _row(
            [
                job.first_seen_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M"),
                display_text(job.company),
                display_text(job.title),
                display_text(job.location),
            ],
            display_text(job.job_url),
        )
        for job in jobs
    )
    if result.page.more_available:
        lines.append(
            f"{len(jobs)} jobs shown; more match (raise --limit, max {MAX_LIMIT})."
        )
    else:
        lines.append(f"{_count(len(jobs), 'job')}.")
    return "\n".join(lines) + "\n"


def _row(cells: Sequence[str], url: str) -> str:
    fitted = [
        _fit(cell, width).ljust(width)
        for cell, (_, width) in zip(cells, TABLE_COLUMNS, strict=True)
    ]
    return "  ".join([*fitted, url]).rstrip()


def _fit(text: str, width: int) -> str:
    if len(text) <= width:
        return text
    return text[: width - len(TRUNCATION_MARK)] + TRUNCATION_MARK


def display_text(value: str | None) -> str:
    """``value`` made safe for one table cell on a terminal.

    Characters in ``UNSAFE_CATEGORIES`` become spaces and whitespace runs
    collapse to one, so a cell is one line of visible text. A missing or
    blank value is ``-``.
    """
    if value is None:
        return "-"
    cleaned = "".join(
        " " if unicodedata.category(char) in UNSAFE_CATEGORIES else char
        for char in value
    )
    return " ".join(cleaned.split()) or "-"


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}" if number == 1 else f"{number} {noun}s"


def render_jobs_jsonl(page: JobPage) -> str:
    """One JSON object per job, one per line, and nothing else.

    ASCII-only: every control, bidi and other non-ASCII character is
    escaped, so the bytes written are printable ASCII. Consumers decode the
    original text and are responsible for displaying it safely.
    """
    return "".join(
        json.dumps(_job_object(job), ensure_ascii=True) + "\n" for job in page.jobs
    )


def _job_object(job: ListedJob) -> dict[str, object]:
    return {
        "source": job.source,
        "source_job_id": job.source_job_id,
        "company": job.company,
        "board_token": job.board_token,
        "title": job.title,
        "location": job.location,
        "department": job.department,
        "job_url": job.job_url,
        "apply_url": job.apply_url,
        "source_published_at": _iso_or_none(job.source_published_at),
        "source_updated_at": _iso_or_none(job.source_updated_at),
        "first_seen_at": _iso(job.first_seen_at),
        "last_seen_at": _iso(job.last_seen_at),
        "is_active": job.is_active,
    }


def _iso(instant: datetime) -> str:
    """UTC, with microseconds, so the value can be passed back to --since."""
    return instant.astimezone(UTC).isoformat(timespec="microseconds")


def _iso_or_none(instant: datetime | None) -> str | None:
    return None if instant is None else _iso(instant)


# --- structured logging ---------------------------------------------------------


class JsonLinesFormatter(logging.Formatter):
    """One JSON object per record, on one line.

    Carries the time, level, logger, message and the allowlisted
    ``LOG_FIELDS`` only. Tracebacks and stack information are never
    rendered: exception text from a driver can contain SQL and row content.
    ``ensure_ascii`` escapes every control character, so a value cannot break
    the line.
    """

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, object] = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(
                timespec="milliseconds"
            ),
            "level": record.levelname,
            "logger": record.name,
            "message": _log_text(record.getMessage()),
        }
        attributes = record.__dict__
        for name in LOG_FIELDS:
            if name in attributes:
                entry[name] = _log_value(attributes[name])
        return json.dumps(entry, ensure_ascii=True)


def _log_value(value: object) -> object:
    if value is None or isinstance(value, bool | int):
        return value
    if isinstance(value, str):
        return _log_text(value)
    # Anything else could serialize to arbitrary content: name its type only.
    return f"<{type(value).__name__}>"


def _log_text(text: str) -> str:
    if len(text) <= MAX_LOG_TEXT:
        return text
    return text[: MAX_LOG_TEXT - 3] + "..."


class _JsonLinesHandler(logging.StreamHandler[TextIO]):
    """A distinct type, so an installation already in place is recognized."""


@contextmanager
def structured_logging(stream: TextIO) -> Iterator[None]:
    """Send ``job_radar`` records at INFO and above to ``stream`` as JSON Lines.

    The handler is attached to the ``job_radar`` logger only, never the root,
    so third-party loggers (SQLAlchemy, httpx) are not captured. If a JSON
    Lines handler is already attached, it is reused rather than duplicated.
    The logger's handlers and level are restored on exit, even on error.
    """
    logger = logging.getLogger(APP_LOGGER)
    if any(isinstance(handler, _JsonLinesHandler) for handler in logger.handlers):
        yield
        return

    handler = _JsonLinesHandler(stream)
    handler.setFormatter(JsonLinesFormatter())
    previous_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        yield
    finally:
        handler.flush()
        logger.removeHandler(handler)
        logger.setLevel(previous_level)
        handler.close()


if __name__ == "__main__":
    raise SystemExit(main())
