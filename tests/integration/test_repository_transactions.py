"""Transaction ownership and concurrency.

Repositories never commit. The caller owns the transaction, which is what lets
the ingestion service give each company its own boundary (ARCHITECTURE).
"""

import threading
from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from job_radar.domain.ingestion import JobUpsertOutcome
from job_radar.domain.job import JobSource, NormalizedJob
from job_radar.persistence.company_repository import CompanyRepository
from job_radar.persistence.job_repository import JobRepository
from job_radar.persistence.session import create_session_factory

pytestmark = pytest.mark.integration

OBSERVED = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
CONCURRENT_TOKEN = "concurrentcorp"
CONCURRENT_JOB_ID = "concurrent-9000"


def make_job(source_job_id: str = "8653419002") -> NormalizedJob:
    return NormalizedJob(
        source=JobSource.GREENHOUSE,
        source_job_id=source_job_id,
        company="ExampleCorp",
        title="Senior Backend Engineer",
        job_url="https://careers.example.com/jobs/1",
        observed_at=OBSERVED,
        raw_data={"id": 1},
    )


# --- transaction ownership --------------------------------------------------


def test_repositories_do_not_commit(schema_engine: Engine) -> None:
    # If a repository committed, the row would survive this rollback.
    connection = schema_engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    company_id = CompanyRepository(session).sync(
        source=JobSource.GREENHOUSE,
        board_token="rollbackcorp",
        display_name="RollbackCorp",
        enabled=True,
    )
    JobRepository(session).upsert(make_job("rollback-1"), company_id=company_id)
    session.close()
    transaction.rollback()
    connection.close()

    with schema_engine.connect() as check:
        survived = check.execute(
            text("SELECT count(*) FROM jobs WHERE source_job_id = 'rollback-1'")
        ).scalar_one()

    assert survived == 0


def test_repository_source_contains_no_commit_calls() -> None:
    # Guards the guard: a stray commit would make the test above pass only by
    # accident of ordering.
    from pathlib import Path

    import job_radar.persistence as persistence

    for module in sorted(Path(persistence.__file__).parent.glob("*repository*.py")):
        text_content = module.read_text(encoding="utf-8")
        assert ".commit()" not in text_content, f"{module.name} commits"
        assert ".rollback()" not in text_content, f"{module.name} rolls back"


# --- concurrency ------------------------------------------------------------


def test_concurrent_upserts_create_exactly_one_row(schema_engine: Engine) -> None:
    """Two writers racing on one identity must not produce two rows.

    This is the reason idempotency lives in UNIQUE (source, source_job_id) and
    not in a read-then-write: the read would see nothing in both writers.
    """
    factory = create_session_factory(schema_engine)
    with schema_engine.begin() as setup:
        setup.execute(
            text("DELETE FROM jobs WHERE source_job_id = :sid"),
            {"sid": CONCURRENT_JOB_ID},
        )
        setup.execute(
            text("DELETE FROM companies WHERE board_token = :t"),
            {"t": CONCURRENT_TOKEN},
        )
        company_id = int(
            setup.execute(
                text(
                    "INSERT INTO companies (source, board_token, display_name)"
                    " VALUES ('greenhouse', :t, 'ConcurrentCorp') RETURNING id"
                ),
                {"t": CONCURRENT_TOKEN},
            ).scalar_one()
        )

    barrier = threading.Barrier(2)
    outcomes: list[JobUpsertOutcome] = []
    failures: list[BaseException] = []
    lock = threading.Lock()

    def writer() -> None:
        try:
            session = factory()
            try:
                barrier.wait(timeout=10)
                result = JobRepository(session).upsert(
                    make_job(CONCURRENT_JOB_ID), company_id=company_id
                )
                session.commit()
                with lock:
                    outcomes.append(result.outcome)
            finally:
                session.close()
        except BaseException as exc:
            with lock:
                failures.append(exc)

    threads = [threading.Thread(target=writer) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    try:
        assert not failures, f"writer raised: {failures}"
        with schema_engine.connect() as check:
            rows = check.execute(
                text("SELECT count(*) FROM jobs WHERE source_job_id = :sid"),
                {"sid": CONCURRENT_JOB_ID},
            ).scalar_one()

        assert rows == 1, "exactly one row survives the race"
        assert outcomes.count(JobUpsertOutcome.INSERTED) == 1, "exactly one insert"
    finally:
        with schema_engine.begin() as cleanup:
            cleanup.execute(
                text("DELETE FROM jobs WHERE source_job_id = :sid"),
                {"sid": CONCURRENT_JOB_ID},
            )
            cleanup.execute(
                text("DELETE FROM companies WHERE board_token = :t"),
                {"t": CONCURRENT_TOKEN},
            )
