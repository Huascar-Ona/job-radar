"""Run status derivation (ADR-0008). Pure domain logic, no database."""

import pytest

from job_radar.domain.crawl import CrawlRunStatus, JobCounters, derive_run_status


@pytest.mark.parametrize(
    ("succeeded", "failed", "expected"),
    [
        (3, 0, CrawlRunStatus.SUCCESS),
        (1, 0, CrawlRunStatus.SUCCESS),
        (2, 1, CrawlRunStatus.PARTIAL_SUCCESS),
        (1, 5, CrawlRunStatus.PARTIAL_SUCCESS),
        (0, 1, CrawlRunStatus.FAILED),
        (0, 3, CrawlRunStatus.FAILED),
        (0, 0, CrawlRunStatus.SUCCESS),
    ],
)
def test_status_derivation(
    succeeded: int, failed: int, expected: CrawlRunStatus
) -> None:
    assert derive_run_status(succeeded=succeeded, failed=failed) is expected


def test_zero_companies_is_success_not_failure() -> None:
    # SPEC states this explicitly: nothing attempted is not a failure.
    assert derive_run_status(succeeded=0, failed=0) is CrawlRunStatus.SUCCESS


def test_negative_counts_are_rejected() -> None:
    with pytest.raises(ValueError, match="negative"):
        derive_run_status(succeeded=-1, failed=0)


def test_counters_default_to_zero() -> None:
    counters = JobCounters()

    assert (counters.fetched, counters.new, counters.existing) == (0, 0, 0)
    assert (counters.updated, counters.invalid) == (0, 0)


def test_counters_are_immutable() -> None:
    counters = JobCounters(fetched=1)

    with pytest.raises(AttributeError):
        counters.fetched = 2  # type: ignore[misc]


def test_counters_reject_negative_values() -> None:
    with pytest.raises(ValueError, match="negative"):
        JobCounters(fetched=-1)


def test_counters_can_be_combined() -> None:
    # A run aggregates its per-company counters.
    total = JobCounters(fetched=3, new=2) + JobCounters(fetched=4, new=1, invalid=1)

    assert total == JobCounters(fetched=7, new=3, existing=0, updated=0, invalid=1)
