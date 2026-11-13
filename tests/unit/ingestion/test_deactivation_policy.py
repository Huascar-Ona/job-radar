"""The deactivation guards as pure functions (ADR-0017)."""

import pytest

from job_radar.ingestion.deactivation import (
    CrawlFacts,
    SkipReason,
    canonical_source_job_id,
    is_large_disappearance,
    precheck,
)


def facts(
    *,
    completeness_verified: bool = True,
    fetched: int = 3,
    valid: int = 3,
    invalid_without_id: int = 0,
) -> CrawlFacts:
    return CrawlFacts(
        completeness_verified=completeness_verified,
        fetched=fetched,
        valid=valid,
        invalid_without_id=invalid_without_id,
    )


# --- precheck ---------------------------------------------------------------------


def test_a_verified_fully_valid_crawl_passes() -> None:
    assert precheck(facts()) is None


def test_an_invalid_posting_with_a_known_id_does_not_block() -> None:
    assert precheck(facts(fetched=3, valid=2, invalid_without_id=0)) is None


@pytest.mark.parametrize(
    ("given", "reason"),
    [
        (facts(completeness_verified=False), SkipReason.COMPLETENESS_UNVERIFIED),
        (facts(fetched=0, valid=0), SkipReason.EMPTY_BOARD),
        (facts(valid=2, invalid_without_id=1), SkipReason.INVALID_POSTING_WITHOUT_ID),
        (facts(fetched=2, valid=0), SkipReason.NO_VALID_OBSERVATIONS),
    ],
    ids=["unverified", "empty", "invalid-without-id", "no-valid"],
)
def test_each_guard_names_its_reason(given: CrawlFacts, reason: SkipReason) -> None:
    assert precheck(given) is reason


def test_guards_are_evaluated_in_the_documented_order() -> None:
    # Everything fails at once: the first guard in ADR-0017's order wins.
    worst = facts(completeness_verified=False, fetched=0, valid=0, invalid_without_id=1)

    assert precheck(worst) is SkipReason.COMPLETENESS_UNVERIFIED
    assert (
        precheck(facts(fetched=0, valid=0, invalid_without_id=1))
        is SkipReason.EMPTY_BOARD
    )
    assert (
        precheck(facts(fetched=2, valid=0, invalid_without_id=2))
        is SkipReason.INVALID_POSTING_WITHOUT_ID
    )


# --- large disappearance ----------------------------------------------------------


@pytest.mark.parametrize(
    ("previously_active", "absent", "large"),
    [
        (10, 5, False),  # exactly half is not more than half
        (10, 6, True),
        (9, 5, True),
        (4, 4, False),  # below the five-job minimum
        (5, 5, True),
        (8, 4, False),
        (100, 51, True),
        (100, 50, False),
        (0, 0, False),  # nothing was active
        (10, 0, False),
    ],
)
def test_large_disappearance_boundaries(
    previously_active: int, absent: int, large: bool
) -> None:
    assert (
        is_large_disappearance(absent=absent, previously_active=previously_active)
        is large
    )


def test_negative_counts_are_a_caller_bug() -> None:
    with pytest.raises(ValueError, match="negative"):
        is_large_disappearance(absent=-1, previously_active=3)


# --- canonical ids ------------------------------------------------------------------


@pytest.mark.parametrize("value", ["123", "4000000000", "-5", "abc-1"])
def test_canonical_ids_are_accepted(value: str) -> None:
    assert canonical_source_job_id(value) == value


@pytest.mark.parametrize(
    "value", [None, "", " ", " 123", "123 ", "\t1", 123, True, 1.0, b"123"]
)
def test_anything_else_is_unknown(value: object) -> None:
    assert canonical_source_job_id(value) is None
