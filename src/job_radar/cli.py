"""Command-line entry point (SPEC, "Command-line interface").

``job-radar`` and ``job-radar --version`` print the installed version.
``job-radar ingest`` runs one crawl over every enabled board.

This module is the composition root. It is the only place where the
Greenhouse collector and normalizer, the database engine and the HTTP client
are wired together, and it owns their lifecycle (ARCHITECTURE, "Transaction
ownership"). The ingestion service owns the transactions.
"""

import argparse
import json
import logging
import sys
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Final, TextIO

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
from job_radar.persistence.ingestion_store import SqlAlchemyStoreFactory
from job_radar.persistence.session import (
    create_database_engine,
    create_session_factory,
)
from job_radar.settings import Settings

#: The run completed, whatever its status (SPEC, "Run reporting").
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
)
#: Backstop length for any text value in a log line.
MAX_LOG_TEXT: Final = 1000


# --- entry point ----------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command line. Returns the process exit code."""
    args = _parser().parse_args(argv)
    if args.command == "ingest" and not args.version:
        return run_ingest(stdout=sys.stdout, stderr=sys.stderr)
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
    return parser


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
) -> int:
    log.error(
        "ingest failed during %s; %s",
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
        f" invalid={counters.invalid}"
    )


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
