"""Engine and session construction.

Connection plumbing only. Queries and upserts belong in the repositories
(company, job and crawl) and in the read-only job query
(``job_radar.persistence.job_query``); the ingestion service's transactions
are opened by the store adapter in ``job_radar.persistence.ingestion_store``.
"""

from collections.abc import Iterator
from contextlib import contextmanager

from psycopg import Connection, IsolationLevel
from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker


def create_database_engine(url: str, *, echo: bool = False) -> Engine:
    """Build an engine for ``url``.

    ``pool_pre_ping`` is enabled so a connection dropped by the server is
    detected and replaced rather than surfacing as a mid-crawl failure.
    """
    return create_engine(url, echo=echo, pool_pre_ping=True, future=True)


def create_read_only_engine(url: str) -> Engine:
    """An engine whose every transaction is ``REPEATABLE READ, READ ONLY``.

    For commands that only read (``job-radar jobs``). The characteristics are
    set on each DBAPI connection when it is opened, while it is idle, and
    psycopg then issues ``BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY``.
    Setting them per checkout instead would fail on a pooled connection that
    ``pool_pre_ping`` has already used. PostgreSQL rejects any write with
    SQLSTATE 25006, and one snapshot serves every statement of a transaction.
    The engine must not be shared with code that writes.
    """
    engine = create_database_engine(url)

    @event.listens_for(engine, "connect")
    def _read_only(dbapi_connection: Connection[object], _record: object) -> None:
        dbapi_connection.isolation_level = IsolationLevel.REPEATABLE_READ
        dbapi_connection.read_only = True

    return engine


@contextmanager
def read_only_session(engine: Engine) -> Iterator[Session]:
    """One transaction on a read-only engine, always rolled back.

    Nothing is committed, whether the block succeeds or raises; the session
    is closed and its connection returned to the pool.
    """
    session = Session(bind=engine)
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Build a session factory bound to ``engine``."""
    return sessionmaker(bind=engine, expire_on_commit=False)


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    """Run a block in a transaction, committing on success, rolling back on error."""
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def check_connection(engine: Engine) -> bool:
    """Return ``True`` when the database answers a trivial query."""
    with engine.connect() as connection:
        # scalar_one() is untyped upstream; bind to object so no Any escapes.
        answer: object = connection.execute(text("SELECT 1")).scalar_one()
    return answer == 1
