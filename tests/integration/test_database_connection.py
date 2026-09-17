"""Database connectivity (ADR-0010: real PostgreSQL, never SQLite)."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, text

from job_radar.persistence.session import (
    check_connection,
    create_session_factory,
    session_scope,
)

pytestmark = pytest.mark.integration


def test_engine_connects(engine: Engine) -> None:
    assert check_connection(engine) is True


def test_backend_is_postgresql(engine: Engine) -> None:
    assert engine.dialect.name == "postgresql"


def test_driver_is_psycopg3(engine: Engine) -> None:
    assert engine.dialect.driver == "psycopg"


def test_timestamptz_round_trips_as_aware_utc(engine: Engine) -> None:
    # ADR-0003: timestamps are timestamptz, and the driver must hand back
    # timezone-aware datetimes rather than naive ones.
    moment = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)

    with engine.connect() as connection:
        stored: object = connection.execute(
            text("SELECT CAST(:moment AS timestamptz)"), {"moment": moment}
        ).scalar_one()

    assert isinstance(stored, datetime)
    assert stored.tzinfo is not None
    assert stored.astimezone(UTC) == moment


def test_offset_bearing_timestamp_normalizes_to_utc(engine: Engine) -> None:
    # Greenhouse emits offsets such as -04:00 and never Z (SPEC, "Observed
    # source behavior"), so the offset must be applied, not discarded.
    with engine.connect() as connection:
        stored: object = connection.execute(
            text("SELECT CAST('2026-08-21T13:02:33-04:00' AS timestamptz)")
        ).scalar_one()

    assert isinstance(stored, datetime)
    assert stored.astimezone(UTC) == datetime(2026, 8, 21, 17, 2, 33, tzinfo=UTC)


def test_session_scope_commits(engine: Engine) -> None:
    factory = create_session_factory(engine)

    with session_scope(factory) as session:
        assert session.execute(text("SELECT 1")).scalar_one() == 1


def test_session_scope_rolls_back_on_error(engine: Engine) -> None:
    factory = create_session_factory(engine)

    def failing_transaction() -> None:
        with session_scope(factory) as session:
            session.execute(text("SELECT 1"))
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        failing_transaction()
