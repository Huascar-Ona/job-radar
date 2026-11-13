"""Read-only job listing (SPEC, "Job listing and query").

Statements only, like the repositories: this module never commits or rolls
back. The caller hands it a session from ``read_only_session``, whose
transaction the database itself keeps read-only.

Selection happens at query time (ADR-0005). Text filters are literal,
case-insensitive substrings; nothing is inferred from ``raw_data``.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from sqlalchemy import ColumnElement, Select, func, or_, select
from sqlalchemy.orm import Session

from job_radar.domain.crawl import CrawlRunStatus
from job_radar.persistence.models import Company, CrawlRun, Job

DEFAULT_LIMIT: Final = 50
MAX_LIMIT: Final = 1000

#: A run with one of these statuses has finished (ADR-0008). A run still
#: ``running`` may have been abandoned; it is never the latest finished run.
FINISHED_STATUSES: Final = (
    CrawlRunStatus.SUCCESS,
    CrawlRunStatus.PARTIAL_SUCCESS,
    CrawlRunStatus.FAILED,
)


@dataclass(frozen=True)
class FinishedRun:
    """The run ``--new-in-last-run`` correlates jobs with."""

    run_id: int
    status: CrawlRunStatus
    started_at: datetime
    #: The run's own count of new jobs, from successful companies only.
    jobs_new: int


@dataclass(frozen=True)
class JobCriteria:
    """What to list. Every given condition must hold (AND).

    ``first_seen_since`` is inclusive. ``first_seen_at`` selects an exact
    instant: a run's ``started_at`` (timestamp correlation, not a recorded
    run-to-job link; SPEC, "Job listing and query").
    """

    first_seen_since: datetime | None = None
    first_seen_at: datetime | None = None
    company: str | None = None
    title: str | None = None
    location: str | None = None
    department: str | None = None
    limit: int = DEFAULT_LIMIT

    def __post_init__(self) -> None:
        if self.first_seen_since is not None and self.first_seen_at is not None:
            msg = "first_seen_since and first_seen_at are mutually exclusive"
            raise ValueError(msg)
        for instant in (self.first_seen_since, self.first_seen_at):
            if instant is not None and instant.utcoffset() is None:
                msg = "timestamps must be timezone-aware"
                raise ValueError(msg)
        for text in (self.company, self.title, self.location, self.department):
            if text is not None and not text:
                msg = "a text filter must not be empty"
                raise ValueError(msg)
        if not 1 <= self.limit <= MAX_LIMIT:
            msg = f"limit must be between 1 and {MAX_LIMIT}"
            raise ValueError(msg)


@dataclass(frozen=True)
class ListedJob:
    """One stored job as the listing reports it."""

    source: str
    source_job_id: str
    company: str
    board_token: str
    title: str
    location: str | None
    department: str | None
    job_url: str
    apply_url: str | None
    source_published_at: datetime | None
    source_updated_at: datetime | None
    first_seen_at: datetime
    last_seen_at: datetime
    is_active: bool


@dataclass(frozen=True)
class JobPage:
    jobs: Sequence[ListedJob]
    #: More jobs matched than ``limit`` allowed.
    more_available: bool


class JobQuery:
    """Reads jobs and crawl runs. Never commits; never writes."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def latest_finished_run(self) -> FinishedRun | None:
        """The finished run with the highest id, if any.

        Highest id, not latest ``started_at``: ids follow the order runs were
        opened and do not depend on the clock.
        """
        row = self._session.execute(
            select(CrawlRun.id, CrawlRun.status, CrawlRun.started_at, CrawlRun.jobs_new)
            .where(CrawlRun.status.in_([str(status) for status in FINISHED_STATUSES]))
            .order_by(CrawlRun.id.desc())
            .limit(1)
        ).one_or_none()
        if row is None:
            return None
        return FinishedRun(
            run_id=int(row.id),
            status=CrawlRunStatus(row.status),
            started_at=row.started_at,
            jobs_new=int(row.jobs_new),
        )

    def unfinished_runs_after(self, run_id: int | None) -> int:
        """How many ``running`` runs were opened after ``run_id``.

        With ``None``, every ``running`` run counts. Their jobs are never
        attributed to the latest finished run.
        """
        statement = select(func.count()).where(
            CrawlRun.status == str(CrawlRunStatus.RUNNING)
        )
        if run_id is not None:
            statement = statement.where(CrawlRun.id > run_id)
        return self._session.execute(statement).scalar_one()

    def list(self, criteria: JobCriteria) -> JobPage:
        """Active jobs matching ``criteria``, most recently discovered first.

        One row beyond the limit is fetched, only to report whether more
        matched.
        """
        rows = self._session.execute(
            _statement(criteria).limit(criteria.limit + 1)
        ).all()
        jobs = [
            ListedJob(
                source=row.source,
                source_job_id=row.source_job_id,
                company=row.display_name,
                board_token=row.board_token,
                title=row.title,
                location=row.location,
                department=row.department,
                job_url=row.job_url,
                apply_url=row.apply_url,
                source_published_at=row.source_published_at,
                source_updated_at=row.source_updated_at,
                first_seen_at=row.first_seen_at,
                last_seen_at=row.last_seen_at,
                is_active=row.is_active,
            )
            for row in rows[: criteria.limit]
        ]
        return JobPage(jobs=tuple(jobs), more_available=len(rows) > criteria.limit)


def _statement(criteria: JobCriteria) -> Select[tuple[object, ...]]:
    """The listing query, without its limit."""
    return (
        select(
            Job.source,
            Job.source_job_id,
            Company.display_name,
            Company.board_token,
            Job.title,
            Job.location,
            Job.department,
            Job.job_url,
            Job.apply_url,
            Job.source_published_at,
            Job.source_updated_at,
            Job.first_seen_at,
            Job.last_seen_at,
            Job.is_active,
        )
        .join(Company, Company.id == Job.company_id)
        .where(*_where(criteria))
        # A total order: first_seen_at can tie, the job's id cannot.
        .order_by(Job.first_seen_at.desc(), Job.id.desc())
    )


def _where(criteria: JobCriteria) -> list[ColumnElement[bool]]:
    conditions: list[ColumnElement[bool]] = [Job.is_active.is_(True)]
    if criteria.first_seen_since is not None:
        conditions.append(Job.first_seen_at >= criteria.first_seen_since)
    if criteria.first_seen_at is not None:
        conditions.append(Job.first_seen_at == criteria.first_seen_at)
    # autoescape: %, _ and the escape character match themselves. A NULL
    # column never matches (NULL ILIKE ... is NULL, which WHERE rejects).
    if criteria.company is not None:
        conditions.append(
            or_(
                Company.display_name.icontains(criteria.company, autoescape=True),
                Company.board_token.icontains(criteria.company, autoescape=True),
            )
        )
    if criteria.title is not None:
        conditions.append(Job.title.icontains(criteria.title, autoescape=True))
    if criteria.location is not None:
        conditions.append(Job.location.icontains(criteria.location, autoescape=True))
    if criteria.department is not None:
        conditions.append(
            Job.department.icontains(criteria.department, autoescape=True)
        )
    return conditions
