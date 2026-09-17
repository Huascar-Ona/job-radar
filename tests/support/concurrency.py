"""A deterministic two-writer race harness.

The interesting window is: writer B's statement has begun (so its snapshot is
taken) while writer A's conflicting row is still uncommitted. B then blocks on
A's row lock, A commits, and B resolves its conflict against a row its snapshot
never saw.

Synchronisation is by observing PostgreSQL's own lock waits, not by sleeping,
so the window is entered deterministically.
"""

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import Engine, text
from sqlalchemy.orm import Session, sessionmaker


@dataclass
class RaceResult:
    """What each writer returned, or raised."""

    winner: Any = None
    loser: Any = None
    winner_error: BaseException | None = None
    loser_error: BaseException | None = None
    errors: list[BaseException] = field(default_factory=list)

    def raise_if_failed(self) -> None:
        if self.errors:
            raise self.errors[0]


def _wait_until_blocked(engine: Engine, timeout: float = 10.0) -> bool:
    """Block until some backend is waiting on a lock, or the timeout expires."""
    deadline = threading.Event()
    waited = 0.0
    step = 0.02
    while waited < timeout:
        with engine.connect() as probe:
            blocked = probe.execute(
                text(
                    "SELECT count(*) FROM pg_stat_activity"
                    " WHERE wait_event_type = 'Lock' AND state = 'active'"
                )
            ).scalar_one()
        if blocked:
            return True
        deadline.wait(step)
        waited += step
    return False


def run_race[T](
    engine: Engine,
    factory: sessionmaker[Session],
    winner_action: Callable[[Session], T],
    loser_action: Callable[[Session], T],
) -> RaceResult:
    """Run two writers so the loser resolves its conflict against a row
    committed after its own statement began.

    ``winner_action`` executes first and holds its transaction open until the
    loser is demonstrably blocked inside PostgreSQL.
    """
    result = RaceResult()
    winner_ready = threading.Event()
    loser_started = threading.Event()
    lock = threading.Lock()

    def winner() -> None:
        session = factory()
        try:
            result.winner = winner_action(session)
            winner_ready.set()
            loser_started.wait(timeout=10)
            _wait_until_blocked(engine)
            session.commit()
        except BaseException as exc:
            with lock:
                result.winner_error = exc
                result.errors.append(exc)
            session.rollback()
            winner_ready.set()
        finally:
            session.close()

    def loser() -> None:
        session = factory()
        try:
            winner_ready.wait(timeout=10)
            loser_started.set()
            result.loser = loser_action(session)
            session.commit()
        except BaseException as exc:
            with lock:
                result.loser_error = exc
                result.errors.append(exc)
            session.rollback()
        finally:
            session.close()

    threads = [threading.Thread(target=winner), threading.Thread(target=loser)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=40)

    return result
