"""Domain errors (SPEC "Failure isolation": log source, company, operation, error)."""

import pytest

from job_radar.domain.errors import (
    CollectorError,
    JobRadarError,
    NormalizationError,
)


def test_collector_error_is_a_job_radar_error() -> None:
    assert issubclass(CollectorError, JobRadarError)


def test_normalization_error_is_a_job_radar_error() -> None:
    assert issubclass(NormalizationError, JobRadarError)


def test_collector_error_carries_failure_context() -> None:
    error = CollectorError(
        source="greenhouse", company="examplecorp", operation="fetch", message="timeout"
    )

    assert error.source == "greenhouse"
    assert error.company == "examplecorp"
    assert error.operation == "fetch"


def test_collector_error_log_context_has_the_four_required_keys() -> None:
    # SPEC mandates exactly this context for an isolated failure.
    context = CollectorError(
        source="greenhouse", company="examplecorp", operation="fetch", message="timeout"
    ).log_context()

    assert set(context) == {"source", "company", "operation", "error"}
    assert context["error"] == "timeout"


def test_collector_error_message_is_readable() -> None:
    error = CollectorError(
        source="greenhouse", company="examplecorp", operation="fetch", message="timeout"
    )

    assert "examplecorp" in str(error)
    assert "timeout" in str(error)


def test_normalization_error_identifies_the_rejected_entry() -> None:
    # A rejected entry is counted as `invalid`; the batch must survive it.
    error = NormalizationError(
        source="greenhouse", source_job_id="8653419002", reason="missing title"
    )

    assert error.source_job_id == "8653419002"
    assert error.reason == "missing title"


def test_normalization_error_allows_unknown_source_job_id() -> None:
    # An entry without an id cannot report one.
    error = NormalizationError(source="greenhouse", source_job_id=None, reason="no id")

    assert error.source_job_id is None


def test_one_except_clause_catches_every_domain_error() -> None:
    # Failure isolation depends on a single catchable base.
    for error in (
        CollectorError(source="s", company="c", operation="o", message="m"),
        NormalizationError(source="s", source_job_id=None, reason="r"),
    ):
        with pytest.raises(JobRadarError):
            raise error
