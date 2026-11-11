"""The SQLAlchemy ingestion store: transaction handling and error safety.

No database. A fake session raises real SQLAlchemy exceptions wrapping real
psycopg errors, each carrying a sensitive marker in its SQL, bound
parameters and driver message: exactly what a failed upsert of ``raw_data``
would carry. None of it may reach a log record, a persisted error message or
a surfaced exception (SPEC, "Persistence errors").
"""

import logging
import traceback
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast

import psycopg.errors
import pytest
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session

from job_radar.domain.errors import PersistenceError
from job_radar.domain.job import JobSource, NormalizedJob
from job_radar.ingestion.ports import StoreFactory
from job_radar.ingestion.service import LOGGER_NAME, IngestionService
from job_radar.persistence.ingestion_store import (
    SqlAlchemyStoreFactory,
    to_persistence_error,
)
from tests.support.clock import FixedClock
from tests.support.ingestion_fakes import FakeCollector, FakeDatabase, boards

T0 = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
SECRET = "SECRET-MARKER-7f3a"


def integrity_error() -> IntegrityError:
    driver = psycopg.errors.UniqueViolation(
        f'duplicate key value violates unique constraint "x"\n'
        f"DETAIL: Key (raw_data)=({SECRET}) already exists."
    )
    return IntegrityError(
        f"INSERT INTO jobs (raw_data) VALUES ('{SECRET}')",  # noqa: S608 - text in an exception, never executed
        {"raw_data": f'{{"content": "{SECRET}"}}'},
        driver,
    )


def operational_error() -> OperationalError:
    driver = psycopg.errors.lookup("08006")(f"connection lost while sending {SECRET}")
    return OperationalError(
        f"INSERT INTO crawl_company_results VALUES ('{SECRET}')",  # noqa: S608 - text in an exception, never executed
        {"error_message": SECRET},
        driver,
    )


def assert_no_secret(*texts: object) -> None:
    for text in texts:
        assert SECRET not in str(text), text


def assert_exception_is_safe(error: BaseException) -> None:
    assert error.__cause__ is None
    assert error.__context__ is None
    assert_no_secret(
        str(error),
        repr(error),
        error.args,
        "".join(traceback.format_exception(error)),
    )


# --- translation ----------------------------------------------------------------


def test_the_fake_database_errors_really_carry_the_marker() -> None:
    # Guards the guard: if the marker were absent from the originals, every
    # "no secret" assertion below would pass vacuously.
    for error in (integrity_error(), operational_error()):
        assert SECRET in str(error)
        assert SECRET in str(error.orig)


def test_translation_keeps_only_category_and_sqlstate() -> None:
    error = to_persistence_error(integrity_error(), operation="persist_company")

    assert error.operation == "persist_company"
    assert error.category == "IntegrityError"
    assert error.sqlstate == "23505"
    assert error.message == "IntegrityError (SQLSTATE 23505)"
    assert_exception_is_safe(error)


def test_translation_without_a_driver_sqlstate() -> None:
    error = to_persistence_error(
        OperationalError(f"SELECT '{SECRET}'", {}, RuntimeError(SECRET)),
        operation="start_run",
    )

    assert error.sqlstate is None
    assert error.message == "OperationalError"
    assert_exception_is_safe(error)


def test_translation_ignores_a_malformed_sqlstate() -> None:
    driver = RuntimeError("boom")
    driver.sqlstate = SECRET  # type: ignore[attr-defined]

    error = to_persistence_error(
        IntegrityError("x", {}, driver), operation="persist_company"
    )

    assert error.sqlstate is None
    assert_exception_is_safe(error)


def test_persistence_error_never_quotes_free_text() -> None:
    error = PersistenceError(operation=SECRET, category=SECRET, sqlstate=SECRET)

    assert error.message == "DatabaseError"
    assert error.operation == "persistence"
    assert_exception_is_safe(error)


# --- a fake session that fails like PostgreSQL --------------------------------


@dataclass
class _Result:
    scalar: object = None
    rowcount: int = 0
    rows: list[object] = field(default_factory=list)

    def scalar_one(self) -> object:
        return self.scalar

    def one(self) -> object:  # pragma: no cover - the upsert always fails here
        return self.rows[0]

    def __iter__(self) -> object:
        return iter(self.rows)


@dataclass
class FakeSession:
    """Answers the repositories' statements, failing where told to."""

    failures: Mapping[str, Callable[[], SQLAlchemyError]] = field(default_factory=dict)
    commit_error: Callable[[], SQLAlchemyError] | None = None
    rollback_error: Callable[[], SQLAlchemyError] | None = None
    executed: list[tuple[str, Mapping[str, object]]] = field(default_factory=list)
    events: list[str] = field(default_factory=list)

    def execute(
        self, statement: object, params: Mapping[str, object] | None = None
    ) -> _Result:
        sql = " ".join(str(statement).split())
        self.executed.append((sql, dict(params or {})))
        for marker, make_error in self.failures.items():
            if marker in sql:
                raise make_error()
        if sql.startswith("INSERT INTO companies"):
            return _Result(scalar=1)
        if sql.startswith("SELECT id, source, board_token"):
            company = SimpleNamespace(
                id=1,
                source="greenhouse",
                board_token="alpha",
                display_name="Alpha",
                created_at=T0,
            )
            return _Result(rows=[company])
        if sql.startswith("INSERT INTO crawl_runs"):
            return _Result(scalar=7)
        return _Result(scalar=1)

    def commit(self) -> None:
        self.events.append("commit")
        if self.commit_error is not None:
            raise self.commit_error()

    def rollback(self) -> None:
        self.events.append("rollback")
        if self.rollback_error is not None:
            raise self.rollback_error()

    def close(self) -> None:
        self.events.append("close")


def factory_for(*sessions: FakeSession) -> SqlAlchemyStoreFactory:
    queue = list(sessions)
    return SqlAlchemyStoreFactory(lambda: cast("Session", queue.pop(0)))


def test_the_adapter_satisfies_the_store_protocol() -> None:
    store: StoreFactory = factory_for()

    assert callable(store)


# --- transactions -------------------------------------------------------------


def test_a_clean_block_commits_and_closes() -> None:
    session = FakeSession()

    with factory_for(session)(operation="start_run") as store:
        assert store.crawls.start(started_at=T0) == 7

    assert session.events == ["commit", "close"]


def test_an_exception_in_the_block_rolls_back_and_propagates_unchanged() -> None:
    session = FakeSession()
    original = RuntimeError("bug")

    with (
        pytest.raises(RuntimeError) as caught,
        factory_for(session)(operation="persist_company"),
    ):
        raise original

    assert caught.value is original
    assert session.events == ["rollback", "close"]


def test_a_failing_statement_surfaces_as_a_safe_persistence_error() -> None:
    session = FakeSession(failures={"INSERT INTO crawl_runs": integrity_error})

    with (
        pytest.raises(PersistenceError) as caught,
        factory_for(session)(operation="start_run") as store,
    ):
        store.crawls.start(started_at=T0)

    assert caught.value.operation == "start_run"
    assert caught.value.message == "IntegrityError (SQLSTATE 23505)"
    assert_exception_is_safe(caught.value)
    assert session.events == ["rollback", "close"]


def test_a_failing_commit_rolls_back_and_surfaces_safely() -> None:
    session = FakeSession(commit_error=operational_error)

    with (
        pytest.raises(PersistenceError) as caught,
        factory_for(session)(operation="finish_run"),
    ):
        pass

    assert caught.value.operation == "finish_run_commit"
    assert caught.value.message == "OperationalError (SQLSTATE 08006)"
    assert_exception_is_safe(caught.value)
    assert session.events == ["commit", "rollback", "close"]


def test_a_failing_rollback_does_not_mask_the_original_error() -> None:
    session = FakeSession(
        failures={"INSERT INTO crawl_runs": integrity_error},
        rollback_error=operational_error,
    )

    with (
        pytest.raises(PersistenceError) as caught,
        factory_for(session)(operation="start_run") as store,
    ):
        store.crawls.start(started_at=T0)

    assert caught.value.category == "IntegrityError"
    assert_exception_is_safe(caught.value)


# --- opening a session ---------------------------------------------------------


def raising_factory(make_error: Callable[[], BaseException]) -> Callable[[], Session]:
    def open_session() -> Session:
        raise make_error()

    return open_session


def test_a_session_that_cannot_be_opened_surfaces_as_a_safe_persistence_error() -> None:
    store = SqlAlchemyStoreFactory(raising_factory(operational_error))

    with pytest.raises(PersistenceError) as caught, store(operation="start_run"):
        pytest.fail("the block must not run without a session")  # pragma: no cover

    assert caught.value.operation == "start_run"
    assert caught.value.message == "OperationalError (SQLSTATE 08006)"
    assert_exception_is_safe(caught.value)


def test_a_programming_error_while_opening_a_session_propagates_unchanged() -> None:
    original = TypeError("bad session factory")
    store = SqlAlchemyStoreFactory(raising_factory(lambda: original))

    with pytest.raises(TypeError) as caught, store(operation="start_run"):
        pytest.fail("the block must not run without a session")  # pragma: no cover

    assert caught.value is original


# --- end to end through the service -------------------------------------------


class FailingUpsertNormalizer:
    """Produces a job whose raw_data carries the marker."""

    def normalize(
        self, posting: object, *, company: str, observed_at: datetime
    ) -> NormalizedJob:
        return NormalizedJob(
            source=JobSource.GREENHOUSE,
            source_job_id="1",
            company=company,
            title="Engineer",
            job_url="https://jobs.example.test/1",
            observed_at=observed_at,
            raw_data={"content": SECRET, "posting": posting},
        )


def service_with(*sessions: FakeSession) -> IngestionService:
    return IngestionService(
        store=factory_for(*sessions),
        collectors={
            JobSource.GREENHOUSE: FakeCollector(FakeDatabase(), {"alpha": [{"id": 1}]})
        },
        normalizers={JobSource.GREENHOUSE: FailingUpsertNormalizer()},
        clock=FixedClock(T0),
    )


def every_log_text(caplog: pytest.LogCaptureFixture) -> list[str]:
    texts: list[str] = []
    for record in caplog.records:
        texts.append(record.getMessage())
        texts.extend(repr(value) for value in record.__dict__.values())
        if record.exc_info:
            texts.append("".join(traceback.format_exception(*record.exc_info)))
    texts.append(caplog.text)
    return texts


def test_driver_text_never_reaches_logs_or_the_failure_record(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    company_tx = FakeSession(failures={"INSERT INTO jobs": integrity_error})
    failure_tx = FakeSession()
    service = service_with(
        FakeSession(), FakeSession(), company_tx, failure_tx, FakeSession()
    )

    summary = service.run(boards(("alpha", "Alpha")))

    [company] = summary.companies
    assert company.error_type == "PersistenceError"
    assert company.error_message == "IntegrityError (SQLSTATE 23505)"
    [(sql, params)] = failure_tx.executed
    assert sql.startswith("INSERT INTO crawl_company_results")
    assert params["error_type"] == "PersistenceError"
    assert params["error_message"] == "IntegrityError (SQLSTATE 23505)"
    assert_no_secret(*every_log_text(caplog), summary)
    assert company_tx.events == ["rollback", "close"]
    assert any(r.name == LOGGER_NAME for r in caplog.records)


def test_a_session_failure_during_reconciliation_aborts_the_run_safely(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    service = IngestionService(
        store=SqlAlchemyStoreFactory(raising_factory(operational_error)),
        collectors={JobSource.GREENHOUSE: FakeCollector(FakeDatabase(), {})},
        normalizers={JobSource.GREENHOUSE: FailingUpsertNormalizer()},
        clock=FixedClock(T0),
    )

    with pytest.raises(PersistenceError) as caught:
        service.run(boards(("alpha", "Alpha")))

    assert caught.value.operation == "reconcile_config"
    assert_exception_is_safe(caught.value)
    assert_no_secret(*every_log_text(caplog))


def test_a_session_failure_while_recording_a_failure_aborts_the_run_safely(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    sessions: list[Callable[[], Session]] = [
        lambda: cast("Session", FakeSession()),  # reconcile
        lambda: cast("Session", FakeSession()),  # start run
        lambda: cast(  # the company: its upsert fails
            "Session", FakeSession(failures={"INSERT INTO jobs": integrity_error})
        ),
        raising_factory(operational_error),  # the failure record: cannot open
    ]
    service = IngestionService(
        store=SqlAlchemyStoreFactory(lambda: sessions.pop(0)()),
        collectors={
            JobSource.GREENHOUSE: FakeCollector(FakeDatabase(), {"alpha": [{"id": 1}]})
        },
        normalizers={JobSource.GREENHOUSE: FailingUpsertNormalizer()},
        clock=FixedClock(T0),
    )

    with pytest.raises(PersistenceError) as caught:
        service.run(boards(("alpha", "Alpha")))

    assert caught.value.operation == "record_company_failure"
    assert caught.value.message == "OperationalError (SQLSTATE 08006)"
    assert_exception_is_safe(caught.value)
    assert_no_secret(*every_log_text(caplog))
    assert any(r.levelno == logging.CRITICAL for r in caplog.records)


def test_driver_text_never_reaches_the_exception_that_aborts_a_run(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    service = service_with(
        FakeSession(),
        FakeSession(),
        FakeSession(failures={"INSERT INTO jobs": integrity_error}),
        FakeSession(failures={"INSERT INTO crawl_company_results": operational_error}),
    )

    with pytest.raises(PersistenceError) as caught:
        service.run(boards(("alpha", "Alpha")))

    assert caught.value.operation == "record_company_failure"
    assert_exception_is_safe(caught.value)
    assert_no_secret(*every_log_text(caplog))
