"""Crawl run and per-company outcome persistence (ADR-0008)."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from job_radar.domain.crawl import CompanyCrawlStatus, CrawlRunStatus, JobCounters
from job_radar.persistence.crawl_repository import CrawlRepository

pytestmark = pytest.mark.integration

STARTED = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
FINISHED = STARTED + timedelta(minutes=3)


@pytest.fixture
def repository(session: Session) -> CrawlRepository:
    return CrawlRepository(session)


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


def run_row(session: Session, run_id: int) -> object:
    return session.execute(
        text("SELECT * FROM crawl_runs WHERE id = :id"), {"id": run_id}
    ).one()


def result_row(session: Session, run_id: int) -> object:
    return session.execute(
        text("SELECT * FROM crawl_company_results WHERE crawl_run_id = :id"),
        {"id": run_id},
    ).one()


# --- run lifecycle ----------------------------------------------------------


def test_start_creates_a_running_crawl(
    repository: CrawlRepository, session: Session
) -> None:
    run_id = repository.start(started_at=STARTED)
    stored = run_row(session, run_id)

    assert stored.status == CrawlRunStatus.RUNNING  # type: ignore[attr-defined]
    assert stored.started_at == STARTED  # type: ignore[attr-defined]
    assert stored.finished_at is None  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "status",
    [CrawlRunStatus.SUCCESS, CrawlRunStatus.PARTIAL_SUCCESS, CrawlRunStatus.FAILED],
)
def test_finish_records_every_terminal_status(
    repository: CrawlRepository, session: Session, status: CrawlRunStatus
) -> None:
    run_id = repository.start(started_at=STARTED)

    repository.finish(
        run_id,
        status=status,
        finished_at=FINISHED,
        companies_total=2,
        companies_succeeded=1,
        companies_failed=1,
        counters=JobCounters(fetched=10, new=4, existing=6, updated=2, invalid=0),
    )
    stored = run_row(session, run_id)

    assert stored.status == status  # type: ignore[attr-defined]
    assert stored.finished_at == FINISHED  # type: ignore[attr-defined]


def test_finish_persists_counters(
    repository: CrawlRepository, session: Session
) -> None:
    run_id = repository.start(started_at=STARTED)

    repository.finish(
        run_id,
        status=CrawlRunStatus.SUCCESS,
        finished_at=FINISHED,
        companies_total=3,
        companies_succeeded=3,
        companies_failed=0,
        counters=JobCounters(fetched=10, new=4, existing=6, updated=2, invalid=1),
    )
    stored = run_row(session, run_id)

    assert stored.companies_total == 3  # type: ignore[attr-defined]
    assert stored.companies_succeeded == 3  # type: ignore[attr-defined]
    assert stored.jobs_fetched == 10  # type: ignore[attr-defined]
    assert stored.jobs_new == 4  # type: ignore[attr-defined]
    assert stored.jobs_existing == 6  # type: ignore[attr-defined]
    assert stored.jobs_updated == 2  # type: ignore[attr-defined]
    assert stored.jobs_invalid == 1  # type: ignore[attr-defined]


# --- per-company outcomes ---------------------------------------------------


def test_company_success_is_recorded(
    repository: CrawlRepository, session: Session, company_id: int
) -> None:
    run_id = repository.start(started_at=STARTED)

    repository.record_company(
        run_id,
        company_id=company_id,
        status=CompanyCrawlStatus.SUCCESS,
        started_at=STARTED,
        finished_at=FINISHED,
        counters=JobCounters(fetched=5, new=5, existing=0, updated=0, invalid=0),
    )
    stored = result_row(session, run_id)

    assert stored.status == CompanyCrawlStatus.SUCCESS  # type: ignore[attr-defined]
    assert stored.jobs_fetched == 5  # type: ignore[attr-defined]
    assert stored.error_type is None  # type: ignore[attr-defined]


def test_company_failure_records_error_context(
    repository: CrawlRepository, session: Session, company_id: int
) -> None:
    # SPEC: a failure must be identifiable after the fact, not only in logs.
    run_id = repository.start(started_at=STARTED)

    repository.record_company(
        run_id,
        company_id=company_id,
        status=CompanyCrawlStatus.FAILED,
        started_at=STARTED,
        finished_at=FINISHED,
        counters=JobCounters(),
        error_type="CollectorError",
        error_message="timeout after 10s",
    )
    stored = result_row(session, run_id)

    assert stored.status == CompanyCrawlStatus.FAILED  # type: ignore[attr-defined]
    assert stored.error_type == "CollectorError"  # type: ignore[attr-defined]
    assert stored.error_message == "timeout after 10s"  # type: ignore[attr-defined]
    assert stored.jobs_fetched == 0  # type: ignore[attr-defined]


def test_last_successful_crawl_is_queryable(
    repository: CrawlRepository, company_id: int
) -> None:
    # The record future deactivation depends on (ADR-0004).
    failed_run = repository.start(started_at=STARTED)
    repository.record_company(
        failed_run,
        company_id=company_id,
        status=CompanyCrawlStatus.FAILED,
        started_at=STARTED,
        finished_at=FINISHED,
        counters=JobCounters(),
        error_type="CollectorError",
        error_message="boom",
    )
    good_run = repository.start(started_at=STARTED - timedelta(days=1))
    repository.record_company(
        good_run,
        company_id=company_id,
        status=CompanyCrawlStatus.SUCCESS,
        started_at=STARTED - timedelta(days=1),
        finished_at=STARTED - timedelta(days=1),
        counters=JobCounters(fetched=3),
    )

    last = repository.last_successful_crawl(company_id)

    assert last == STARTED - timedelta(days=1)


def test_no_successful_crawl_returns_none(
    repository: CrawlRepository, company_id: int
) -> None:
    run_id = repository.start(started_at=STARTED)
    repository.record_company(
        run_id,
        company_id=company_id,
        status=CompanyCrawlStatus.FAILED,
        started_at=STARTED,
        finished_at=FINISHED,
        counters=JobCounters(),
        error_type="CollectorError",
        error_message="boom",
    )

    assert repository.last_successful_crawl(company_id) is None


# --- database invariants still hold -----------------------------------------


def test_negative_counters_are_rejected_before_reaching_the_database(
    repository: CrawlRepository,
) -> None:
    # Defence in depth: JobCounters refuses negatives in the domain, so the
    # repository can never send one. The CHECK constraint that backs this up is
    # exercised with raw SQL in test_schema.py.
    repository.start(started_at=STARTED)

    with pytest.raises(ValueError, match="negative"):
        JobCounters(fetched=-1)


def test_one_result_per_company_per_run(
    repository: CrawlRepository, company_id: int
) -> None:
    from sqlalchemy.exc import IntegrityError

    run_id = repository.start(started_at=STARTED)
    repository.record_company(
        run_id,
        company_id=company_id,
        status=CompanyCrawlStatus.SUCCESS,
        started_at=STARTED,
        finished_at=FINISHED,
        counters=JobCounters(),
    )

    with pytest.raises(IntegrityError):
        repository.record_company(
            run_id,
            company_id=company_id,
            status=CompanyCrawlStatus.SUCCESS,
            started_at=STARTED,
            finished_at=FINISHED,
            counters=JobCounters(),
        )
