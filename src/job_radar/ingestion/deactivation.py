"""When a company's absent jobs may be deactivated (ADR-0017).

Pure policy: no I/O. The ingestion service gathers the facts from one
company's crawl and asks these functions whether deactivation is safe; the
counts it passes are stored jobs, never fetched postings.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

#: A disappearance is "large" only if it also affects at least this many jobs.
LARGE_DISAPPEARANCE_MIN_JOBS: Final = 5


class SkipReason(StrEnum):
    """Why a company's absent jobs were not deactivated in this crawl."""

    COMPLETENESS_UNVERIFIED = "completeness_unverified"
    EMPTY_BOARD = "empty_board"
    INVALID_POSTING_WITHOUT_ID = "invalid_posting_without_id"
    NO_VALID_OBSERVATIONS = "no_valid_observations"
    LARGE_DISAPPEARANCE = "large_disappearance"


@dataclass(frozen=True, slots=True)
class CrawlFacts:
    """What one company's successful crawl observed."""

    completeness_verified: bool
    #: Postings the collector returned.
    fetched: int
    #: Postings that normalized into a job.
    valid: int
    #: Invalid postings whose id is unknown or not canonical.
    invalid_without_id: int


def precheck(facts: CrawlFacts) -> SkipReason | None:
    """The first guard that does not need the stored job counts, if any fails.

    Evaluated in the order ADR-0017 lists them, so the reason is stable.
    """
    if not facts.completeness_verified:
        return SkipReason.COMPLETENESS_UNVERIFIED
    if facts.fetched == 0:
        return SkipReason.EMPTY_BOARD
    if facts.invalid_without_id:
        return SkipReason.INVALID_POSTING_WITHOUT_ID
    if facts.valid == 0:
        return SkipReason.NO_VALID_OBSERVATIONS
    return None


def is_large_disappearance(*, absent: int, previously_active: int) -> bool:
    """More than half of the previously active jobs, and at least five.

    Integer arithmetic: exactly half is not more than half.
    """
    if absent < 0 or previously_active < 0:
        msg = f"counts may not be negative: {absent=}, {previously_active=}"
        raise ValueError(msg)
    return 2 * absent > previously_active and absent >= LARGE_DISAPPEARANCE_MIN_JOBS


def canonical_source_job_id(value: object) -> str | None:
    """``value`` if it can protect a stored job, otherwise ``None``.

    A normalizer reports an invalid posting's id only when it is exactly the
    id a valid posting would store (``Normalizer`` contract). This is the
    service's own check on that promise: a non-blank string without
    surrounding whitespace, as ``NormalizedJob.source_job_id`` stores it.
    """
    if not isinstance(value, str) or not value or value != value.strip():
        return None
    return value
