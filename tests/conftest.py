"""Shared fixtures."""

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Connection, Engine
from sqlalchemy.orm import Session

from job_radar.persistence.session import create_database_engine
from job_radar.settings import Settings
from tests.support import database as db
from tests.support.clock import FixedClock

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def project_root() -> Path:
    return PROJECT_ROOT


@pytest.fixture(scope="session")
def settings() -> Settings:
    return Settings()


@pytest.fixture(scope="session")
def engine(settings: Settings) -> Iterator[Engine]:
    """Engine against the docker-compose PostgreSQL instance."""
    created = create_database_engine(settings.database_url)
    try:
        yield created
    finally:
        created.dispose()


# --- schema fixtures --------------------------------------------------------

SCHEMA_DB = "jobradar_schema_test"
MIGRATION_DB = "jobradar_migration_test"


@pytest.fixture(scope="session")
def schema_database_url(settings: Settings) -> str:
    """A database with every migration applied, shared by schema tests."""
    url = db.derive_database_url(settings.database_url, SCHEMA_DB)
    db.ensure_database(url)
    db.drop_all_tables(url)
    db.upgrade(url)
    return url


@pytest.fixture(scope="session")
def migration_database_url(settings: Settings) -> str:
    """An empty database reserved for destructive migration lifecycle tests."""
    url = db.derive_database_url(settings.database_url, MIGRATION_DB)
    db.ensure_database(url)
    db.drop_all_tables(url)
    return url


@pytest.fixture(scope="session")
def schema_engine(schema_database_url: str) -> Iterator[Engine]:
    created = create_database_engine(schema_database_url)
    try:
        yield created
    finally:
        created.dispose()


@pytest.fixture
def connection(schema_engine: Engine) -> Iterator[Connection]:
    """A connection whose transaction is rolled back after each test."""
    with schema_engine.connect() as conn:
        transaction = conn.begin()
        try:
            yield conn
        finally:
            transaction.rollback()


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(datetime(2026, 9, 15, 12, 0, tzinfo=UTC))


@pytest.fixture
def session(schema_engine: Engine) -> Iterator[Session]:
    """A Session whose work is rolled back after each test.

    The test owns the transaction; repositories never commit (ARCHITECTURE,
    "Transaction ownership").
    """
    connection = schema_engine.connect()
    transaction = connection.begin()
    orm_session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield orm_session
    finally:
        orm_session.close()
        transaction.rollback()
        connection.close()
