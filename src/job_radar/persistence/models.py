"""SQLAlchemy models for the four persistence tables.

Structure only: no query, upsert or ingestion behavior lives here.

PostgreSQL-native types are used deliberately (JSONB, timestamptz). The design
depends on them, and ADR-0010 rules out designing for SQLite compatibility.
"""

from datetime import datetime
from typing import Any, Final

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from job_radar.domain.crawl import CompanyCrawlStatus, CrawlRunStatus
from job_radar.persistence.base import Base

# Counters shared by runs and per-company results.
JOB_COUNTERS: Final = (
    "jobs_fetched",
    "jobs_new",
    "jobs_existing",
    "jobs_updated",
    "jobs_invalid",
)
COMPANY_COUNTERS: Final = (
    "companies_total",
    "companies_succeeded",
    "companies_failed",
)


def _status_check(
    column: str, statuses: type[CrawlRunStatus | CompanyCrawlStatus]
) -> str:
    """SQL fragment restricting ``column`` to the documented status values."""
    allowed = ", ".join(f"'{status.value}'" for status in statuses)
    return f"{column} IN ({allowed})"


def _non_negative(*columns: str) -> str:
    """SQL fragment requiring every named counter to be zero or greater."""
    return " AND ".join(f"{column} >= 0" for column in columns)


def _counter() -> Mapped[int]:
    return mapped_column(Integer, nullable=False, server_default="0")


def _timestamp(*, nullable: bool = False) -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), nullable=nullable)


class Company(Base):
    """A configured job board.

    Derived from config/boards.yml (ADR-0012), never edited at runtime. Rows
    are disabled rather than deleted, so historical jobs and crawl outcomes
    keep their foreign keys.
    """

    __tablename__ = "companies"
    __table_args__ = (
        UniqueConstraint("source", "board_token", name="uq_companies_source_token"),
        # Referenced by the composite foreign key from jobs. Not an identity
        # constraint: id alone is already unique.
        UniqueConstraint("id", "source", name="uq_companies_id_source"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    board_token: Mapped[str] = mapped_column(Text, nullable=False)
    #: Mutable metadata. Renaming carries no identity and touches no job rows.
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    is_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class Job(Base):
    """A job as stored.

    Identity is ``(source, source_job_id)``; ``company_id`` is not part of it.
    """

    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("source", "source_job_id", name="uq_jobs_source_job_id"),
        # Identity depends on source_job_id, so it may not be blank. `~ '\\S'`
        # requires at least one non-whitespace character; a btrim()-based check
        # with the default character list would let tabs and newlines through.
        CheckConstraint(r"source_job_id ~ '\S'", name="source_job_id_not_blank"),
        # Composite: a job's source must equal its company's source. Two
        # columns that must agree need a constraint that makes them agree.
        # RESTRICT: a company with jobs may be neither deleted nor have its
        # source changed out from under them.
        ForeignKeyConstraint(
            ["company_id", "source"],
            ["companies.id", "companies.source"],
            ondelete="RESTRICT",
            onupdate="RESTRICT",
            name="fk_jobs_company_id_source_companies",
        ),
        Index("ix_jobs_company_id", "company_id"),
        Index("ix_jobs_first_seen_at", "first_seen_at"),
        Index("ix_jobs_last_seen_at", "last_seen_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    #: Text, not numeric: observed Greenhouse ids exceed 32-bit range and other
    #: sources use UUIDs (ADR-0002).
    source_job_id: Mapped[str] = mapped_column(Text, nullable=False)
    #: Paired with `source` in the composite foreign key declared above.
    company_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    title: Mapped[str] = mapped_column(Text, nullable=False)
    location: Mapped[str | None] = mapped_column(Text, nullable=True)
    department: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    job_url: Mapped[str] = mapped_column(Text, nullable=False)
    apply_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: Reported by the source. Null when unavailable; never inferred.
    source_published_at: Mapped[datetime | None] = _timestamp(nullable=True)
    source_updated_at: Mapped[datetime | None] = _timestamp(nullable=True)

    #: Set from the observation's observed_at on insert, then never assigned.
    first_seen_at: Mapped[datetime] = _timestamp()
    #: Set from observed_at on every observation.
    last_seen_at: Mapped[datetime] = _timestamp()

    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )

    raw_data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    #: Hash over the source-derived fields; guards against rewriting a large
    #: unchanged payload on every crawl (ADR-0011).
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    #: The value `content_hash` held immediately before the last observation
    #: that touched this row, captured inside the conflict branch. NULL means
    #: the row has never been re-observed. It exists so the upsert can classify
    #: its own outcome from the live row rather than from a snapshot read
    #: (ADR-0015).
    previous_content_hash: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class CrawlRun(Base):
    """One execution of the ingestion pipeline over all enabled companies."""

    __tablename__ = "crawl_runs"
    __table_args__ = (
        CheckConstraint(_status_check("status", CrawlRunStatus), name="status"),
        CheckConstraint(
            _non_negative(*COMPANY_COUNTERS, *JOB_COUNTERS),
            name="counters_non_negative",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[datetime] = _timestamp()
    finished_at: Mapped[datetime | None] = _timestamp(nullable=True)

    companies_total: Mapped[int] = _counter()
    companies_succeeded: Mapped[int] = _counter()
    companies_failed: Mapped[int] = _counter()

    jobs_fetched: Mapped[int] = _counter()
    jobs_new: Mapped[int] = _counter()
    jobs_existing: Mapped[int] = _counter()
    jobs_updated: Mapped[int] = _counter()
    jobs_invalid: Mapped[int] = _counter()


class CrawlCompanyResult(Base):
    """The outcome of one company within one run.

    This is the record future deactivation depends on: it may only reason
    about absence after a crawl of that company that succeeded (ADR-0004).
    """

    __tablename__ = "crawl_company_results"
    __table_args__ = (
        UniqueConstraint(
            "crawl_run_id", "company_id", name="uq_crawl_company_results_run_company"
        ),
        CheckConstraint(_status_check("status", CompanyCrawlStatus), name="status"),
        CheckConstraint(_non_negative(*JOB_COUNTERS), name="counters_non_negative"),
        Index(
            "ix_crawl_company_results_company_status_finished",
            "company_id",
            "status",
            "finished_at",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    crawl_run_id: Mapped[int] = mapped_column(
        BigInteger,
        # CASCADE: a result has no meaning without its run.
        ForeignKey("crawl_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    company_id: Mapped[int] = mapped_column(
        BigInteger,
        # RESTRICT: crawl history outlives configuration changes.
        ForeignKey("companies.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[datetime] = _timestamp()
    finished_at: Mapped[datetime | None] = _timestamp(nullable=True)

    jobs_fetched: Mapped[int] = _counter()
    jobs_new: Mapped[int] = _counter()
    jobs_existing: Mapped[int] = _counter()
    jobs_updated: Mapped[int] = _counter()
    jobs_invalid: Mapped[int] = _counter()

    error_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
