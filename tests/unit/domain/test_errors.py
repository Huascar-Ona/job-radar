"""Domain errors (SPEC "Failure isolation": log source, company, operation, error)."""

import pytest

from job_radar.domain.errors import (
    CollectorError,
    JobRadarError,
    NormalizationError,
    PersistenceError,
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
        PersistenceError(operation="o", category="OperationalError"),
    ):
        with pytest.raises(JobRadarError):
            raise error


# --- PersistenceError (SPEC "Persistence errors") ---------------------------


def test_persistence_error_message_is_category_and_sqlstate() -> None:
    error = PersistenceError(
        operation="persist_company", category="IntegrityError", sqlstate="23505"
    )

    assert error.message == "IntegrityError (SQLSTATE 23505)"
    assert error.log_context() == {
        "operation": "persist_company",
        "error": "IntegrityError (SQLSTATE 23505)",
    }
    assert str(error) == "persist_company failed: IntegrityError (SQLSTATE 23505)"


def test_persistence_error_without_sqlstate() -> None:
    error = PersistenceError(operation="start_run", category="OperationalError")

    assert error.message == "OperationalError"
    assert error.sqlstate is None


@pytest.mark.parametrize(
    "category",
    ["", "Integrity Error", "IntegrityError: Key (raw_data)=(x)", "x" * 65, "1Bad"],
)
def test_persistence_error_replaces_a_category_that_is_not_a_class_name(
    category: str,
) -> None:
    error = PersistenceError(operation="start_run", category=category)

    assert error.category == "DatabaseError"
    assert error.message == "DatabaseError"


@pytest.mark.parametrize("sqlstate", ["", "2350", "235051", "23 05", "abcde", "x\n1"])
def test_persistence_error_drops_a_malformed_sqlstate(sqlstate: str) -> None:
    error = PersistenceError(
        operation="start_run", category="IntegrityError", sqlstate=sqlstate
    )

    assert error.sqlstate is None
    assert error.message == "IntegrityError"


def test_persistence_error_replaces_an_operation_that_is_not_a_name() -> None:
    error = PersistenceError(operation="drop table jobs", category="IntegrityError")

    assert error.operation == "persistence"


def test_persistence_error_message_is_bounded() -> None:
    longest = PersistenceError(operation="o" * 64, category="C" * 64, sqlstate="23505")

    assert len(longest.message) <= 100
    assert len(str(longest)) <= 200
