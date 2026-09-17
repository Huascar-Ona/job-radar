"""Engine and session construction.

Connection plumbing only. Query and upsert behavior belongs in repositories,
which do not exist yet.
"""

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker


def create_database_engine(url: str, *, echo: bool = False) -> Engine:
    """Build an engine for ``url``.

    ``pool_pre_ping`` is enabled so a connection dropped by the server is
    detected and replaced rather than surfacing as a mid-crawl failure.
    """
    return create_engine(url, echo=echo, pool_pre_ping=True, future=True)


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
