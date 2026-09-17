"""The production Clock abstraction.

ADR-0003: one consistent instant per crawl, never a bare datetime.now().
"""

from datetime import datetime, timedelta

from job_radar.domain.clock import Clock, SystemClock


def test_system_clock_returns_aware_datetime() -> None:
    assert SystemClock().now().tzinfo is not None


def test_system_clock_returns_utc() -> None:
    assert SystemClock().now().utcoffset() == timedelta(0)


def test_system_clock_advances() -> None:
    clock = SystemClock()

    assert clock.now() <= clock.now()


def test_system_clock_satisfies_the_protocol() -> None:
    clock: Clock = SystemClock()

    assert isinstance(clock.now(), datetime)


def test_domain_exposes_no_test_double() -> None:
    # FixedClock belongs to the test suite; production must not ship a clock
    # that never moves.
    import job_radar.domain.clock as production_clock

    assert not hasattr(production_clock, "FixedClock")
