"""Helpers for running tests against real PostgreSQL databases (ADR-0010).

Schema tests and migration-lifecycle tests each get their own database, so a
destructive downgrade cannot disturb a suite running against the same schema.
"""

import re
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

PROJECT_ROOT = Path(__file__).resolve().parents[2]

_SAFE_DATABASE_NAME = re.compile(r"^[a-z0-9_]+$")


def derive_database_url(base_url: str, database: str) -> str:
    """Return ``base_url`` pointed at a different database on the same server."""
    if not _SAFE_DATABASE_NAME.match(database):
        msg = f"unsafe test database name: {database!r}"
        raise ValueError(msg)
    return (
        make_url(base_url).set(database=database).render_as_string(hide_password=False)
    )


def ensure_database(url: str) -> None:
    """Create the database in ``url`` when it does not exist yet."""
    target = make_url(url)
    name = target.database
    if name is None or not _SAFE_DATABASE_NAME.match(name):
        msg = f"unsafe test database name: {name!r}"
        raise ValueError(msg)

    admin = create_engine(target.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            exists = connection.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": name}
            ).scalar()
            if not exists:
                # Name is validated above; PostgreSQL cannot parameterize DDL.
                connection.execute(text(f'CREATE DATABASE "{name}"'))
    finally:
        admin.dispose()


def drop_all_tables(url: str) -> None:
    """Return the database to an empty state."""
    engine = create_engine(url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            connection.execute(text("DROP SCHEMA public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))
    finally:
        engine.dispose()


def alembic_config(url: str) -> Config:
    """An Alembic config bound to ``url`` rather than to the ambient settings."""
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", url)
    return config


def upgrade(url: str, revision: str = "head") -> None:
    command.upgrade(alembic_config(url), revision)


def downgrade(url: str, revision: str = "base") -> None:
    command.downgrade(alembic_config(url), revision)


def table_names(url: str) -> set[str]:
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            rows = connection.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            ).scalars()
            return set(rows)
    finally:
        engine.dispose()


def drop_table(url: str, table: str) -> None:
    """Drop one table, to prove drift detection actually detects."""
    if not _SAFE_DATABASE_NAME.match(table):
        msg = f"unsafe table name: {table!r}"
        raise ValueError(msg)
    engine = create_engine(url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            connection.execute(text(f'DROP TABLE IF EXISTS "{table}" CASCADE'))
    finally:
        engine.dispose()


def foreign_keys(url: str, table: str) -> list[dict[str, object]]:
    """Reflected foreign keys for ``table``."""
    engine = create_engine(url)
    try:
        return [dict(fk) for fk in inspect(engine).get_foreign_keys(table)]
    finally:
        engine.dispose()


def unique_constraint_names(url: str, table: str) -> set[str]:
    engine = create_engine(url)
    try:
        return {str(c["name"]) for c in inspect(engine).get_unique_constraints(table)}
    finally:
        engine.dispose()


def check_constraint_names(url: str, table: str) -> set[str]:
    engine = create_engine(url)
    try:
        return {str(c["name"]) for c in inspect(engine).get_check_constraints(table)}
    finally:
        engine.dispose()


def column_names(url: str, table: str) -> set[str]:
    engine = create_engine(url)
    try:
        return {str(c["name"]) for c in inspect(engine).get_columns(table)}
    finally:
        engine.dispose()
