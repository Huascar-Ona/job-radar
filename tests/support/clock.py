"""Test doubles for the Clock abstraction.

FixedClock lives here rather than in the domain: production code needs only
the Clock protocol and SystemClock, and shipping a test double in the
application package would invite production use of a clock that never moves.
"""

from datetime import UTC, datetime, timedelta


class FixedClock:
    """A clock that stands still, so tests can assert exact timestamps.

    Satisfies job_radar.domain.clock.Clock structurally.
    """

    def __init__(self, moment: datetime) -> None:
        self._moment = _require_aware(moment)

    def now(self) -> datetime:
        return self._moment

    def advance(self, delta: timedelta) -> None:
        """Move the clock forward, to simulate a later crawl."""
        self._moment = self._moment + delta

    def set(self, moment: datetime) -> None:
        """Jump to an explicit instant."""
        self._moment = _require_aware(moment)


def _require_aware(moment: datetime) -> datetime:
    """Reject naive datetimes; convert aware ones to UTC."""
    if moment.tzinfo is None or moment.utcoffset() is None:
        msg = f"clock requires a timezone-aware datetime, got naive {moment!r}"
        raise ValueError(msg)
    return moment.astimezone(UTC)
