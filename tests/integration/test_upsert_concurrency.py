"""Upsert classification under genuine concurrency.

UNIQUE (source, source_job_id) is the concurrency primitive and is not in
question here. What these tests pin down is whether the *classification* the
repository reports matches what was actually persisted, when a conflicting row
is committed after the losing writer's statement has already begun.
"""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session, sessionmaker

from job_radar.domain.ingestion import JobUpsertOutcome, JobUpsertResult
from job_radar.domain.job import JobSource, NormalizedJob
from job_radar.persistence.job_repository import JobRepository
from job_radar.persistence.session import create_session_factory
from tests.support.concurrency import run_race

pytestmark = pytest.mark.integration

FIRST = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
LATER = FIRST + timedelta(hours=1)
JOB_ID = "race-identity"


def make_job(title: str = "Engineer", observed_at: datetime = FIRST) -> NormalizedJob:
    return NormalizedJob(
        source=JobSource.GREENHOUSE,
        source_job_id=JOB_ID,
        company="RaceCorp",
        title=title,
        job_url="https://careers.example.com/jobs/race",
        observed_at=observed_at,
        raw_data={"id": "race"},
    )


@pytest.fixture
def factory(schema_engine: Engine) -> sessionmaker[Session]:
    return create_session_factory(schema_engine)


@pytest.fixture
def companies(schema_engine: Engine) -> Iterator[tuple[int, int]]:
    """Two committed companies and a clean slate for the raced identity."""
    with schema_engine.begin() as setup:
        setup.execute(text("DELETE FROM jobs WHERE source_job_id = :j"), {"j": JOB_ID})
        setup.execute(
            text("DELETE FROM companies WHERE board_token IN ('racea', 'raceb')")
        )
        first = int(
            setup.execute(
                text(
                    "INSERT INTO companies (source, board_token, display_name)"
                    " VALUES ('greenhouse', 'racea', 'RaceA') RETURNING id"
                )
            ).scalar_one()
        )
        second = int(
            setup.execute(
                text(
                    "INSERT INTO companies (source, board_token, display_name)"
                    " VALUES ('greenhouse', 'raceb', 'RaceB') RETURNING id"
                )
            ).scalar_one()
        )
    try:
        yield first, second
    finally:
        with schema_engine.begin() as cleanup:
            cleanup.execute(
                text("DELETE FROM jobs WHERE source_job_id = :j"), {"j": JOB_ID}
            )
            cleanup.execute(
                text("DELETE FROM companies WHERE board_token IN ('racea', 'raceb')")
            )


def stored(engine: Engine) -> object:
    with engine.connect() as connection:
        return connection.execute(
            text("SELECT * FROM jobs WHERE source_job_id = :j"), {"j": JOB_ID}
        ).one()


def row_count(engine: Engine) -> int:
    with engine.connect() as connection:
        return int(
            connection.execute(
                text("SELECT count(*) FROM jobs WHERE source_job_id = :j"),
                {"j": JOB_ID},
            ).scalar_one()
        )


def outcomes(winner: JobUpsertResult, loser: JobUpsertResult) -> list[JobUpsertOutcome]:
    return [winner.outcome, loser.outcome]


# --- 1: absent identity, same company, same content -------------------------


def test_same_content_race_yields_one_insert_and_one_unchanged(
    schema_engine: Engine,
    factory: sessionmaker[Session],
    companies: tuple[int, int],
) -> None:
    company_id, _ = companies

    race = run_race(
        schema_engine,
        factory,
        lambda s: JobRepository(s).upsert(make_job(), company_id=company_id),
        lambda s: JobRepository(s).upsert(
            make_job(observed_at=LATER), company_id=company_id
        ),
    )
    race.raise_if_failed()

    assert row_count(schema_engine) == 1
    assert outcomes(race.winner, race.loser) == [
        JobUpsertOutcome.INSERTED,
        JobUpsertOutcome.UNCHANGED,
    ], "the loser changed nothing, so it must not report UPDATED"


# --- 2: absent identity, same company, different content --------------------


def test_different_content_race_yields_one_insert_and_one_update(
    schema_engine: Engine,
    factory: sessionmaker[Session],
    companies: tuple[int, int],
) -> None:
    # The loser genuinely overwrote the winner's content, so UPDATED is the
    # semantically correct classification: it describes what was persisted.
    company_id, _ = companies

    race = run_race(
        schema_engine,
        factory,
        lambda s: JobRepository(s).upsert(
            make_job(title="Engineer"), company_id=company_id
        ),
        lambda s: JobRepository(s).upsert(
            make_job(title="Staff Engineer", observed_at=LATER), company_id=company_id
        ),
    )
    race.raise_if_failed()

    assert row_count(schema_engine) == 1
    assert outcomes(race.winner, race.loser) == [
        JobUpsertOutcome.INSERTED,
        JobUpsertOutcome.UPDATED,
    ]
    assert stored(schema_engine).title == "Staff Engineer"  # type: ignore[attr-defined]


# --- 3: absent identity, different companies --------------------------------


def test_company_conflict_race_surfaces_an_anomaly_not_an_exception(
    schema_engine: Engine,
    factory: sessionmaker[Session],
    companies: tuple[int, int],
) -> None:
    first, second = companies

    race = run_race(
        schema_engine,
        factory,
        lambda s: JobRepository(s).upsert(make_job(), company_id=first),
        lambda s: JobRepository(s).upsert(
            make_job(title="Hijack", observed_at=LATER), company_id=second
        ),
    )
    race.raise_if_failed()

    assert row_count(schema_engine) == 1
    assert race.winner.outcome is JobUpsertOutcome.INSERTED
    assert race.loser.outcome is JobUpsertOutcome.COMPANY_CONFLICT


def test_company_conflict_race_reports_both_company_ids(
    schema_engine: Engine,
    factory: sessionmaker[Session],
    companies: tuple[int, int],
) -> None:
    first, second = companies

    race = run_race(
        schema_engine,
        factory,
        lambda s: JobRepository(s).upsert(make_job(), company_id=first),
        lambda s: JobRepository(s).upsert(
            make_job(observed_at=LATER), company_id=second
        ),
    )
    race.raise_if_failed()

    assert race.loser.persisted_company_id == first
    assert race.loser.incoming_company_id == second


def test_company_conflict_race_does_not_reassign_the_row(
    schema_engine: Engine,
    factory: sessionmaker[Session],
    companies: tuple[int, int],
) -> None:
    first, second = companies

    race = run_race(
        schema_engine,
        factory,
        lambda s: JobRepository(s).upsert(make_job(), company_id=first),
        lambda s: JobRepository(s).upsert(
            make_job(title="Hijack", observed_at=LATER), company_id=second
        ),
    )
    race.raise_if_failed()
    row = stored(schema_engine)

    assert row.company_id == first  # type: ignore[attr-defined]
    assert row.title == "Engineer"  # type: ignore[attr-defined]
    assert row.last_seen_at == FIRST  # type: ignore[attr-defined]


# --- 4: existing row, two concurrent updates --------------------------------


def test_concurrent_updates_preserve_first_seen_at(
    schema_engine: Engine,
    factory: sessionmaker[Session],
    companies: tuple[int, int],
) -> None:
    company_id, _ = companies
    seed = factory()
    JobRepository(seed).upsert(make_job(), company_id=company_id)
    seed.commit()
    seed.close()

    race = run_race(
        schema_engine,
        factory,
        lambda s: JobRepository(s).upsert(
            make_job(title="A", observed_at=LATER), company_id=company_id
        ),
        lambda s: JobRepository(s).upsert(
            make_job(title="B", observed_at=LATER + timedelta(hours=1)),
            company_id=company_id,
        ),
    )
    race.raise_if_failed()
    row = stored(schema_engine)

    assert row_count(schema_engine) == 1
    assert row.first_seen_at == FIRST  # type: ignore[attr-defined]


def test_concurrent_updates_classify_what_was_persisted(
    schema_engine: Engine,
    factory: sessionmaker[Session],
    companies: tuple[int, int],
) -> None:
    company_id, _ = companies
    seed = factory()
    JobRepository(seed).upsert(make_job(), company_id=company_id)
    seed.commit()
    seed.close()

    race = run_race(
        schema_engine,
        factory,
        lambda s: JobRepository(s).upsert(
            make_job(title="A", observed_at=LATER), company_id=company_id
        ),
        lambda s: JobRepository(s).upsert(
            make_job(title="B", observed_at=LATER + timedelta(hours=1)),
            company_id=company_id,
        ),
    )
    race.raise_if_failed()
    row = stored(schema_engine)

    assert outcomes(race.winner, race.loser) == [
        JobUpsertOutcome.UPDATED,
        JobUpsertOutcome.UPDATED,
    ]
    assert row.title == "B", "the last serialized writer wins"  # type: ignore[attr-defined]
    assert row.last_seen_at == LATER + timedelta(hours=1)  # type: ignore[attr-defined]


def test_concurrent_identical_updates_report_unchanged_for_the_loser(
    schema_engine: Engine,
    factory: sessionmaker[Session],
    companies: tuple[int, int],
) -> None:
    # Both writers submit the same new content. The second changed nothing.
    company_id, _ = companies
    seed = factory()
    JobRepository(seed).upsert(make_job(), company_id=company_id)
    seed.commit()
    seed.close()

    race = run_race(
        schema_engine,
        factory,
        lambda s: JobRepository(s).upsert(
            make_job(title="Same", observed_at=LATER), company_id=company_id
        ),
        lambda s: JobRepository(s).upsert(
            make_job(title="Same", observed_at=LATER + timedelta(hours=1)),
            company_id=company_id,
        ),
    )
    race.raise_if_failed()

    assert outcomes(race.winner, race.loser) == [
        JobUpsertOutcome.UPDATED,
        JobUpsertOutcome.UNCHANGED,
    ]
