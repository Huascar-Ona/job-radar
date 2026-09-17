"""Crawl outcome vocabulary (ADR-0008).

These statuses are domain concepts defined by SPEC, not persistence details.
They live here so the ingestion service and the schema cannot drift apart on
what a run outcome is called.
"""

from dataclasses import dataclass, fields
from enum import StrEnum
from typing import Self


class CrawlRunStatus(StrEnum):
    """Outcome of one crawl run over all enabled companies."""

    RUNNING = "running"
    SUCCESS = "success"
    PARTIAL_SUCCESS = "partial_success"
    FAILED = "failed"


class CompanyCrawlStatus(StrEnum):
    """Outcome of one company within a run.

    There is no partial state: a company was either crawled or it was not.
    """

    SUCCESS = "success"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class JobCounters:
    """Per-company or aggregated job counters for one crawl.

    `fetched = new + existing + invalid` is the identity SPEC requires; it is
    the caller's job to maintain it, since only the caller knows what it saw.
    """

    fetched: int = 0
    new: int = 0
    existing: int = 0
    updated: int = 0
    invalid: int = 0

    def __post_init__(self) -> None:
        for field in fields(self):
            if getattr(self, field.name) < 0:
                msg = f"counter {field.name} may not be negative"
                raise ValueError(msg)

    def __add__(self, other: Self) -> "JobCounters":
        return JobCounters(
            fetched=self.fetched + other.fetched,
            new=self.new + other.new,
            existing=self.existing + other.existing,
            updated=self.updated + other.updated,
            invalid=self.invalid + other.invalid,
        )


def derive_run_status(*, succeeded: int, failed: int) -> CrawlRunStatus:
    """Classify a finished run from its per-company outcomes (ADR-0008).

    A run over zero companies is a success: nothing was attempted, so nothing
    failed.
    """
    if succeeded < 0 or failed < 0:
        msg = f"company counts may not be negative: {succeeded=}, {failed=}"
        raise ValueError(msg)
    if failed == 0:
        return CrawlRunStatus.SUCCESS
    if succeeded == 0:
        return CrawlRunStatus.FAILED
    return CrawlRunStatus.PARTIAL_SUCCESS
