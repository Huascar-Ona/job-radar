"""The job listing query and the read-only transaction, against PostgreSQL.

SPEC, "Job listing and query". Query tests run in the shared ``session``
fixture: one transaction on ``jobradar_schema_test`` that is always rolled
back, so the rows they seed, and the run rows a few of them delete to get a
known state, never survive. Other test modules leave committed rows behind,
so these tests scope their assertions by unique board tokens or by instants
far in the future, never by assuming an empty table.

The read-only tests use their own engine from ``create_read_only_engine``.
Every write they attempt must be refused by PostgreSQL, so they change
nothing either.
"""

from collections.abc import Iterator, Sequence
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from job_radar.domain.crawl import CrawlRunStatus
from job_radar.persistence.job_query import (
    FinishedRun,
    JobCriteria,
    JobQuery,
    ListedJob,
)
from job_radar.persistence.session import create_read_only_engine, read_only_session
from tests.support.pipeline import assert_schema_test_database

pytestmark = pytest.mark.integration

ALPHA = "jobquery-alpha"
BETA = "jobquery-beta"
#: Later than any instant another module stores, so these rows lead the
#: unfiltered listing.
T = datetime(2061, 3, 4, 5, 6, 7, 891011, tzinfo=UTC)
ONE_MICROSECOND = timedelta(microseconds=1)


# --- seeding --------------------------------------------------------------------


def company(session: Session, token: str, name: str) -> int:
    return int(
        session.execute(
            text(
                "INSERT INTO companies (source, board_token, display_name)"
                " VALUES ('greenhouse', :token, :name) RETURNING id"
            ),
            {"token": token, "name": name},
        ).scalar_one()
    )


def seed_job(
    session: Session,
    company_id: int,
    source_job_id: str,
    *,
    first_seen_at: datetime = T,
    last_seen_at: datetime | None = None,
    title: str = "Software Engineer",
    location: str | None = None,
    department: str | None = None,
    apply_url: str | None = None,
    published: datetime | None = None,
) -> None:
    session.execute(
        text(
            "INSERT INTO jobs (source, source_job_id, company_id, title, location,"
            " department, job_url, apply_url, source_published_at, first_seen_at,"
            " last_seen_at, raw_data, content_hash)"
            " VALUES ('greenhouse', :id, :company_id, :title, :location,"
            " :department, :url, :apply_url, :published, :first, :last,"
            " '{}'::jsonb, 'hash')"
        ),
        {
            "id": source_job_id,
            "company_id": company_id,
            "title": title,
            "location": location,
            "department": department,
            "url": f"https://boards.example/{source_job_id}",
            "apply_url": apply_url,
            "published": published,
            "first": first_seen_at,
            "last": last_seen_at or first_seen_at,
        },
    )


def seed_run(
    session: Session, status: CrawlRunStatus, started_at: datetime, new: int = 0
) -> int:
    finished = None if status is CrawlRunStatus.RUNNING else started_at
    return int(
        session.execute(
            text(
                "INSERT INTO crawl_runs (status, started_at, finished_at, jobs_new)"
                " VALUES (:status, :started, :finished, :new) RETURNING id"
            ),
            {
                "status": str(status),
                "started": started_at,
                "finished": finished,
                "new": new,
            },
        ).scalar_one()
    )


def forget_all_runs(session: Session) -> None:
    """Only inside the rolled-back test transaction, on the test database."""
    assert_schema_test_database(session.connection())
    session.execute(text("DELETE FROM crawl_runs"))


def ids(jobs: Sequence[ListedJob]) -> list[str]:
    return [job.source_job_id for job in jobs]


@pytest.fixture
def query(session: Session) -> JobQuery:
    assert_schema_test_database(session.connection())
    return JobQuery(session)


@pytest.fixture
def alpha(session: Session) -> int:
    return company(session, ALPHA, "Acme Corp")


@pytest.fixture
def beta(session: Session) -> int:
    return company(session, BETA, "Globex")


# --- runs -----------------------------------------------------------------------


def test_no_finished_run(session: Session, query: JobQuery) -> None:
    forget_all_runs(session)
    seed_run(session, CrawlRunStatus.RUNNING, T)

    assert query.latest_finished_run() is None
    assert query.unfinished_runs_after(None) == 1


def test_latest_finished_run_is_the_highest_finished_id(
    session: Session, query: JobQuery
) -> None:
    seed_run(session, CrawlRunStatus.SUCCESS, T, new=3)
    # Started earlier by the clock, opened later: the id decides (D3).
    failed = seed_run(session, CrawlRunStatus.FAILED, T - timedelta(days=1))
    seed_run(session, CrawlRunStatus.RUNNING, T + timedelta(days=1))
    seed_run(session, CrawlRunStatus.RUNNING, T + timedelta(days=2))

    assert query.latest_finished_run() == FinishedRun(
        run_id=failed,
        status=CrawlRunStatus.FAILED,
        started_at=T - timedelta(days=1),
        jobs_new=0,
    )
    assert query.unfinished_runs_after(failed) == 2


def test_partial_success_is_a_finished_run(session: Session, query: JobQuery) -> None:
    run = seed_run(session, CrawlRunStatus.PARTIAL_SUCCESS, T, new=2)

    latest = query.latest_finished_run()

    assert latest is not None
    assert (latest.run_id, latest.status, latest.jobs_new) == (
        run,
        CrawlRunStatus.PARTIAL_SUCCESS,
        2,
    )


# --- selection ------------------------------------------------------------------


def test_run_correlation_selects_jobs_first_seen_at_the_runs_start(
    session: Session, query: JobQuery, alpha: int
) -> None:
    seed_job(session, alpha, "new-in-run", first_seen_at=T)
    seed_job(
        session,
        alpha,
        "re-observed",
        first_seen_at=T - timedelta(days=1),
        last_seen_at=T,
    )
    seed_job(session, alpha, "later", first_seen_at=T + ONE_MICROSECOND)

    page = query.list(JobCriteria(first_seen_at=T, company=ALPHA))

    assert ids(page.jobs) == ["new-in-run"]


def test_since_is_inclusive_to_the_microsecond(
    session: Session, query: JobQuery, alpha: int
) -> None:
    seed_job(session, alpha, "at", first_seen_at=T)
    seed_job(session, alpha, "before", first_seen_at=T - ONE_MICROSECOND)
    seed_job(session, alpha, "after", first_seen_at=T + ONE_MICROSECOND)

    page = query.list(JobCriteria(first_seen_since=T, company=ALPHA))

    assert ids(page.jobs) == ["after", "at"]


def test_unfiltered_listing_covers_the_whole_history_newest_first(
    session: Session, query: JobQuery, alpha: int, beta: int
) -> None:
    seed_job(session, alpha, "a", first_seen_at=T)
    seed_job(session, beta, "b", first_seen_at=T + timedelta(hours=1))
    seed_job(session, alpha, "c", first_seen_at=T - timedelta(hours=1))

    page = query.list(JobCriteria(limit=3))

    assert ids(page.jobs) == ["b", "a", "c"]


def test_equal_first_seen_falls_back_to_the_newest_id_and_limit_reports_more(
    session: Session, query: JobQuery, alpha: int
) -> None:
    for job_id in ("first", "second", "third"):
        seed_job(session, alpha, job_id, first_seen_at=T)

    two = query.list(JobCriteria(company=ALPHA, limit=2))
    three = query.list(JobCriteria(company=ALPHA, limit=3))

    assert ids(two.jobs) == ["third", "second"]
    assert two.more_available is True
    assert ids(three.jobs) == ["third", "second", "first"]
    assert three.more_available is False


def test_inactive_jobs_are_not_listed(
    session: Session, query: JobQuery, alpha: int
) -> None:
    seed_job(session, alpha, "active")
    seed_job(session, alpha, "inactive")
    session.execute(
        text("UPDATE jobs SET is_active = false WHERE source_job_id = 'inactive'")
    )

    assert ids(query.list(JobCriteria(company=ALPHA)).jobs) == ["active"]


# --- text filters ---------------------------------------------------------------


def test_text_filters_ignore_ascii_case(
    session: Session, query: JobQuery, alpha: int
) -> None:
    seed_job(session, alpha, "match", title="Senior BACKEND Engineer")
    seed_job(session, alpha, "other", title="Designer")

    page = query.list(JobCriteria(company=ALPHA, title="backend"))

    assert ids(page.jobs) == ["match"]


@pytest.mark.parametrize(
    ("needle", "matching", "not_matching"),
    [
        ("50%", "50% remote", "500 remote"),
        ("a_b", "a_b team", "axb team"),
        ("C:\\x", "C:\\x drive", "C:x drive"),
        ("a/b", "a/b team", "ab team"),
    ],
)
def test_wildcards_and_escapes_match_themselves(
    session: Session,
    query: JobQuery,
    alpha: int,
    needle: str,
    matching: str,
    not_matching: str,
) -> None:
    seed_job(session, alpha, "match", title=matching)
    seed_job(session, alpha, "other", title=not_matching)

    page = query.list(JobCriteria(company=ALPHA, title=needle))

    assert ids(page.jobs) == ["match"]


def test_null_location_and_department_never_match(
    session: Session, query: JobQuery, alpha: int
) -> None:
    seed_job(session, alpha, "located", location="Remote", department="Platform")
    seed_job(session, alpha, "nowhere", location=None, department=None)

    assert ids(query.list(JobCriteria(company=ALPHA, location="e")).jobs) == ["located"]
    assert ids(query.list(JobCriteria(company=ALPHA, department="a")).jobs) == [
        "located"
    ]


def test_accents_are_significant(session: Session, query: JobQuery, alpha: int) -> None:
    seed_job(session, alpha, "accented", location="México")

    assert ids(query.list(JobCriteria(company=ALPHA, location="México")).jobs) == [
        "accented"
    ]
    assert query.list(JobCriteria(company=ALPHA, location="Mexico")).jobs == ()


def test_company_matches_display_name_or_board_token(
    session: Session, query: JobQuery, alpha: int, beta: int
) -> None:
    seed_job(session, alpha, "acme-job")
    seed_job(session, beta, "globex-job")

    assert ids(query.list(JobCriteria(company="acme corp")).jobs) == ["acme-job"]
    assert ids(query.list(JobCriteria(company="jobquery-be")).jobs) == ["globex-job"]


def test_filters_combine_with_and(
    session: Session, query: JobQuery, alpha: int
) -> None:
    seed_job(session, alpha, "both", title="Data Engineer", location="LATAM")
    seed_job(session, alpha, "title-only", title="Data Engineer", location="Berlin")
    seed_job(session, alpha, "location-only", title="Designer", location="LATAM")

    page = query.list(JobCriteria(company=ALPHA, title="engineer", location="latam"))

    assert ids(page.jobs) == ["both"]


def test_listed_job_carries_the_stored_values(
    session: Session, query: JobQuery, alpha: int
) -> None:
    published = datetime(2061, 3, 1, 12, 0, tzinfo=UTC)
    seed_job(
        session,
        alpha,
        "full",
        title="Engineer",
        location="Remote",
        department="Platform",
        apply_url="https://apply.example/full",
        published=published,
        last_seen_at=T + timedelta(hours=2),
    )

    [listed] = query.list(JobCriteria(company=ALPHA)).jobs

    assert listed == ListedJob(
        source="greenhouse",
        source_job_id="full",
        company="Acme Corp",
        board_token=ALPHA,
        title="Engineer",
        location="Remote",
        department="Platform",
        job_url="https://boards.example/full",
        apply_url="https://apply.example/full",
        source_published_at=published,
        source_updated_at=None,
        first_seen_at=T,
        last_seen_at=T + timedelta(hours=2),
        is_active=True,
    )
    assert listed.first_seen_at.utcoffset() == timedelta(0)


# --- read-only transaction ------------------------------------------------------


@pytest.fixture
def read_only_engine(schema_database_url: str) -> Iterator[Engine]:
    engine = create_read_only_engine(schema_database_url)
    try:
        yield engine
    finally:
        engine.dispose()


def _settings(session: Session) -> tuple[str, str]:
    isolation = session.execute(text("SHOW transaction_isolation")).scalar_one()
    read_only = session.execute(text("SHOW transaction_read_only")).scalar_one()
    return str(isolation), str(read_only)


def test_the_transaction_is_repeatable_read_and_read_only(
    read_only_engine: Engine,
) -> None:
    with read_only_session(read_only_engine) as session:
        assert_schema_test_database(session.connection())
        assert _settings(session) == ("repeatable read", "on")


def test_a_reused_pooled_connection_stays_read_only(read_only_engine: Engine) -> None:
    # The second checkout is pre-pinged; the characteristics must survive it.
    for _ in range(2):
        with read_only_session(read_only_engine) as session:
            assert _settings(session) == ("repeatable read", "on")


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO companies (source, board_token, display_name)"
        " VALUES ('greenhouse', 'jobquery-refused', 'Refused')",
        "UPDATE jobs SET is_active = false",
        "DELETE FROM crawl_runs",
    ],
)
def test_postgresql_refuses_every_write(
    read_only_engine: Engine, statement: str
) -> None:
    with read_only_session(read_only_engine) as session:
        assert_schema_test_database(session.connection())
        with pytest.raises(DBAPIError) as refused:
            session.execute(text(statement))

    assert getattr(refused.value.orig, "sqlstate", None) == "25006"


def test_the_query_runs_inside_the_read_only_transaction(
    read_only_engine: Engine,
) -> None:
    with read_only_session(read_only_engine) as session:
        query = JobQuery(session)
        query.latest_finished_run()
        page = query.list(JobCriteria(limit=1))
        assert _settings(session) == ("repeatable read", "on")

    assert len(page.jobs) <= 1
