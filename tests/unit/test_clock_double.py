"""The FixedClock test double must itself be correct.

Every timestamp assertion in the suite rests on it.
"""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from job_radar.domain.clock import Clock
from tests.support.clock import FixedClock


def test_returns_the_injected_instant() -> None:
    moment = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)

    assert FixedClock(moment).now() == moment


def test_does_not_drift() -> None:
    # A crawl must stamp one instant across every job it touches.
    clock = FixedClock(datetime(2026, 9, 15, 12, 0, tzinfo=UTC))

    assert clock.now() == clock.now()


def test_rejects_naive_datetimes() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        FixedClock(datetime(2026, 9, 15, 12, 0))  # noqa: DTZ001


def test_converts_to_utc() -> None:
    moment = datetime(2026, 8, 21, 13, 2, 33, tzinfo=timezone(-timedelta(hours=4)))

    assert FixedClock(moment).now() == datetime(2026, 8, 21, 17, 2, 33, tzinfo=UTC)


def test_can_be_advanced() -> None:
    clock = FixedClock(datetime(2026, 9, 15, 12, 0, tzinfo=UTC))

    clock.advance(timedelta(hours=1))

    assert clock.now() == datetime(2026, 9, 15, 13, 0, tzinfo=UTC)


def test_can_be_set() -> None:
    clock = FixedClock(datetime(2026, 9, 15, 12, 0, tzinfo=UTC))

    clock.set(datetime(2026, 10, 1, 8, 0, tzinfo=UTC))

    assert clock.now() == datetime(2026, 10, 1, 8, 0, tzinfo=UTC)


def test_satisfies_the_clock_protocol() -> None:
    clock: Clock = FixedClock(datetime(2026, 9, 15, 12, 0, tzinfo=UTC))

    assert isinstance(clock.now(), datetime)
