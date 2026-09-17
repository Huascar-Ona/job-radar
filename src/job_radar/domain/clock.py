"""Time as an injected dependency.

A crawl must stamp one consistent instant across every job it touches, and
tests must be able to assert exact timestamps without sleeping (ADR-0003).
Production code therefore never calls ``datetime.now()`` directly: it takes a
Clock.

Test doubles live in the test suite, not here.
"""

from datetime import UTC, datetime
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    """Supplies the current instant."""

    def now(self) -> datetime:
        """Return the current instant as a timezone-aware UTC datetime."""
        ...


class SystemClock:
    """The real clock."""

    def now(self) -> datetime:
        return datetime.now(UTC)
