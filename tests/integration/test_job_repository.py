"""JobRepository upsert semantics.

Idempotency is enforced by PostgreSQL's UNIQUE (source, source_job_id) via
INSERT ... ON CONFLICT, never by a read-then-write in Python.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from job_radar.domain.ingestion import JobUpsertOutcome
from job_radar.domain.job import JobSource, NormalizedJob
from job_radar.persistence.job_repository import JobRepository

pytestmark = pytest.mark.integration

FIRST = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
LATER = FIRST + timedelta(days=1)


@pytest.fixture
def repository(session: Session) -> JobRepository:
    return JobRepository(session)


@pytest.fixture
def company_id(session: Session) -> int:
    return int(
        session.execute(
            text(
                "INSERT INTO companies (source, board_token, display_name)"
                " VALUES ('greenhouse', 'examplecorp', 'ExampleCorp') RETURNING id"
            )
        ).scalar_one()
    )


@pytest.fixture
def other_company_id(session: Session) -> int:
    return int(
        session.execute(
            text(
                "INSERT INTO companies (source, board_token, display_name)"
                " VALUES ('greenhouse', 'othercorp', 'OtherCorp') RETURNING id"
            )
        ).scalar_one()
    )


def make_job(**overrides: Any) -> NormalizedJob:  # noqa: ANN401
    fields: dict[str, Any] = {
        "source": JobSource.GREENHOUSE,
        "source_job_id": "8653419002",
        "company": "ExampleCorp",
        "title": "Senior Backend Engineer",
        "job_url": "https://careers.example.com/jobs/1",
        "observed_at": FIRST,
        "raw_data": {"id": 8653419002},
    }
    fields.update(overrides)
    return NormalizedJob(**fields)


def row(session: Session, source_job_id: str = "8653419002") -> Any:  # noqa: ANN401
    return session.execute(
        text("SELECT * FROM jobs WHERE source_job_id = :sid"), {"sid": source_job_id}
    ).one()


def count(session: Session) -> int:
    return int(session.execute(text("SELECT count(*) FROM jobs")).scalar_one())


# --- first observation ------------------------------------------------------


def test_first_observation_inserts_one_row(
    repository: JobRepository, session: Session, company_id: int
) -> None:
    result = repository.upsert(make_job(), company_id=company_id)

    assert result.outcome is JobUpsertOutcome.INSERTED
    assert count(session) == 1


def test_first_seen_at_equals_observed_at_on_insert(
    repository: JobRepository, session: Session, company_id: int
) -> None:
    repository.upsert(make_job(), company_id=company_id)

    assert row(session).first_seen_at == FIRST


def test_last_seen_at_equals_observed_at_on_insert(
    repository: JobRepository, session: Session, company_id: int
) -> None:
    repository.upsert(make_job(), company_id=company_id)

    assert row(session).last_seen_at == FIRST


def test_insert_persists_the_normalized_fields(
    repository: JobRepository, session: Session, company_id: int
) -> None:
    repository.upsert(
        make_job(location="Remote - Mexico", department="Engineering"),
        company_id=company_id,
    )
    stored = row(session)

    assert stored.title == "Senior Backend Engineer"
    assert stored.location == "Remote - Mexico"
    assert stored.department == "Engineering"
    assert stored.job_url == "https://careers.example.com/jobs/1"


def test_insert_persists_raw_data_and_content_hash(
    repository: JobRepository, session: Session, company_id: int
) -> None:
    repository.upsert(make_job(), company_id=company_id)
    stored = row(session)

    assert stored.raw_data == {"id": 8653419002}
    assert stored.content_hash


def test_is_active_is_true_on_insert(
    repository: JobRepository, session: Session, company_id: int
) -> None:
    # ADR-0004: every successfully ingested job is active in the MVP.
    repository.upsert(make_job(), company_id=company_id)

    assert row(session).is_active is True


# --- second observation -----------------------------------------------------


def test_identical_second_observation_creates_no_second_row(
    repository: JobRepository, session: Session, company_id: int
) -> None:
    repository.upsert(make_job(), company_id=company_id)
    repository.upsert(make_job(observed_at=LATER), company_id=company_id)

    assert count(session) == 1


def test_identical_second_observation_reports_unchanged(
    repository: JobRepository, company_id: int
) -> None:
    repository.upsert(make_job(), company_id=company_id)

    result = repository.upsert(make_job(observed_at=LATER), company_id=company_id)

    assert result.outcome is JobUpsertOutcome.UNCHANGED


def test_second_observation_preserves_first_seen_at(
    repository: JobRepository, session: Session, company_id: int
) -> None:
    # The invariant the whole observation model rests on.
    repository.upsert(make_job(), company_id=company_id)
    repository.upsert(make_job(observed_at=LATER), company_id=company_id)

    assert row(session).first_seen_at == FIRST


def test_second_observation_advances_last_seen_at(
    repository: JobRepository, session: Session, company_id: int
) -> None:
    repository.upsert(make_job(), company_id=company_id)
    repository.upsert(make_job(observed_at=LATER), company_id=company_id)

    assert row(session).last_seen_at == LATER


def test_changed_source_fields_are_updated(
    repository: JobRepository, session: Session, company_id: int
) -> None:
    repository.upsert(make_job(), company_id=company_id)

    repository.upsert(
        make_job(observed_at=LATER, title="Staff Backend Engineer", location="Remote"),
        company_id=company_id,
    )
    stored = row(session)

    assert stored.title == "Staff Backend Engineer"
    assert stored.location == "Remote"


def test_changed_observation_reports_updated(
    repository: JobRepository, company_id: int
) -> None:
    repository.upsert(make_job(), company_id=company_id)

    result = repository.upsert(
        make_job(observed_at=LATER, title="Staff Backend Engineer"),
        company_id=company_id,
    )

    assert result.outcome is JobUpsertOutcome.UPDATED


def test_raw_data_is_replaced_when_content_changes(
    repository: JobRepository, session: Session, company_id: int
) -> None:
    repository.upsert(make_job(), company_id=company_id)

    repository.upsert(
        make_job(observed_at=LATER, title="Staff", raw_data={"id": 1, "v": 2}),
        company_id=company_id,
    )

    assert row(session).raw_data == {"id": 1, "v": 2}


def test_content_hash_changes_with_the_source_fields(
    repository: JobRepository, session: Session, company_id: int
) -> None:
    repository.upsert(make_job(), company_id=company_id)
    before = row(session).content_hash

    repository.upsert(
        make_job(observed_at=LATER, title="Staff Backend Engineer"),
        company_id=company_id,
    )

    assert row(session).content_hash != before


def test_content_hash_ignores_observed_at(
    repository: JobRepository, session: Session, company_id: int
) -> None:
    # Re-observing an unchanged job must not look like a change.
    repository.upsert(make_job(), company_id=company_id)
    before = row(session).content_hash

    repository.upsert(make_job(observed_at=LATER), company_id=company_id)

    assert row(session).content_hash == before


def test_unchanged_observation_leaves_raw_data_in_place(
    repository: JobRepository, session: Session, company_id: int
) -> None:
    # ADR-0011: an unchanged job must not rewrite its ~10 KB payload.
    repository.upsert(make_job(), company_id=company_id)

    repository.upsert(make_job(observed_at=LATER), company_id=company_id)

    assert row(session).raw_data == {"id": 8653419002}


def test_reobservation_keeps_the_job_active(
    repository: JobRepository, session: Session, company_id: int
) -> None:
    repository.upsert(make_job(), company_id=company_id)
    session.execute(text("UPDATE jobs SET is_active = false"))

    repository.upsert(make_job(observed_at=LATER), company_id=company_id)

    assert row(session).is_active is True


# --- nullable source timestamps ---------------------------------------------


def test_absent_source_timestamps_persist_as_null(
    repository: JobRepository, session: Session, company_id: int
) -> None:
    # ADR-0003: never inferred from observation time.
    repository.upsert(make_job(), company_id=company_id)
    stored = row(session)

    assert stored.source_published_at is None
    assert stored.source_updated_at is None


def test_present_source_timestamps_persist(
    repository: JobRepository, session: Session, company_id: int
) -> None:
    published = datetime(2026, 7, 28, 13, 47, 45, tzinfo=UTC)
    updated = datetime(2026, 8, 21, 17, 2, 33, tzinfo=UTC)

    repository.upsert(
        make_job(source_published_at=published, source_updated_at=updated),
        company_id=company_id,
    )
    stored = row(session)

    assert stored.source_published_at == published
    assert stored.source_updated_at == updated


def test_source_timestamps_may_become_null_again(
    repository: JobRepository, session: Session, company_id: int
) -> None:
    repository.upsert(
        make_job(source_updated_at=datetime(2026, 8, 21, tzinfo=UTC)),
        company_id=company_id,
    )

    repository.upsert(
        make_job(observed_at=LATER, title="Changed", source_updated_at=None),
        company_id=company_id,
    )

    assert row(session).source_updated_at is None


# --- the company-change anomaly ---------------------------------------------


def test_conflicting_company_surfaces_an_anomaly(
    repository: JobRepository, company_id: int, other_company_id: int
) -> None:
    repository.upsert(make_job(), company_id=company_id)

    result = repository.upsert(
        make_job(observed_at=LATER, company="OtherCorp"), company_id=other_company_id
    )

    assert result.outcome is JobUpsertOutcome.COMPANY_CONFLICT
    assert result.is_anomaly is True


def test_anomaly_reports_both_company_ids(
    repository: JobRepository, company_id: int, other_company_id: int
) -> None:
    repository.upsert(make_job(), company_id=company_id)

    result = repository.upsert(make_job(observed_at=LATER), company_id=other_company_id)

    assert result.persisted_company_id == company_id
    assert result.incoming_company_id == other_company_id


def test_anomaly_does_not_mutate_the_existing_row(
    repository: JobRepository, session: Session, company_id: int, other_company_id: int
) -> None:
    repository.upsert(make_job(), company_id=company_id)
    before = row(session)._asdict()

    repository.upsert(
        make_job(observed_at=LATER, title="Hijacked"), company_id=other_company_id
    )

    assert row(session)._asdict() == before


def test_anomaly_creates_no_additional_row(
    repository: JobRepository, session: Session, company_id: int, other_company_id: int
) -> None:
    repository.upsert(make_job(), company_id=company_id)

    repository.upsert(make_job(observed_at=LATER), company_id=other_company_id)

    assert count(session) == 1


def test_anomaly_carries_structured_log_context(
    repository: JobRepository, company_id: int, other_company_id: int
) -> None:
    repository.upsert(make_job(), company_id=company_id)

    result = repository.upsert(make_job(observed_at=LATER), company_id=other_company_id)

    assert set(result.log_context()) >= {
        "source",
        "source_job_id",
        "operation",
        "error",
    }


def test_normal_outcomes_are_not_anomalies(
    repository: JobRepository, company_id: int
) -> None:
    inserted = repository.upsert(make_job(), company_id=company_id)
    unchanged = repository.upsert(make_job(observed_at=LATER), company_id=company_id)

    assert inserted.is_anomaly is False
    assert unchanged.is_anomaly is False


# --- identity scope ---------------------------------------------------------


def test_identity_is_scoped_by_source_not_by_company(
    repository: JobRepository, session: Session, company_id: int
) -> None:
    # The repository writes `source` from the observation, so stored identity
    # is provider-scoped (ADR-0014). Cross-source identity itself is proven at
    # the schema level, where raw SQL can write a source the domain does not
    # yet support.
    repository.upsert(make_job(), company_id=company_id)

    assert row(session).source == "greenhouse"


def test_domain_refuses_an_unsupported_source() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        make_job(source="lever")


def test_unexpected_integrity_errors_are_not_swallowed(
    repository: JobRepository,
) -> None:
    # Only the identity conflict belongs to the upsert path. A job pointing at
    # a company that does not exist must raise, not be reported as "existing".
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        repository.upsert(make_job(), company_id=999999)
