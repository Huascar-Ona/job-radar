"""Deactivation inside the ingestion service, against in-memory fakes.

ADR-0017: a company's absent jobs are deactivated in its success transaction
only when every guard holds; a skip is logged and is not a failure. Real SQL
behavior is covered by tests/integration/test_ingestion_pipeline_deactivation.py.
"""

import logging
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import pytest

from job_radar.collectors.base import FetchedBoard
from job_radar.domain.crawl import CompanyCrawlStatus, CrawlRunStatus, JobCounters
from job_radar.domain.errors import CollectorError, NormalizationError
from job_radar.domain.job import JobSource, NormalizedJob
from job_radar.ingestion.service import LOGGER_NAME, IngestionService
from job_radar.ingestion.summary import CompanyCrawlSummary
from tests.support.clock import FixedClock
from tests.support.ingestion_fakes import (
    GREENHOUSE,
    FakeCollector,
    FakeDatabase,
    FakeJobRow,
    FakeNormalizer,
    boards,
    posting,
)

T0 = datetime(2026, 11, 12, 9, 0, tzinfo=UTC)
CONFIG = boards(("alpha", "Alpha"))


class Harness:
    def __init__(self, normalizer: FakeNormalizer | None = None) -> None:
        self.db = FakeDatabase()
        self.collector = FakeCollector(self.db, {})
        self.clock = FixedClock(T0)
        self.service = IngestionService(
            store=self.db,
            collectors={JobSource.GREENHOUSE: self.collector},
            normalizers={JobSource.GREENHOUSE: normalizer or FakeNormalizer()},
            clock=self.clock,
        )

    def serve(self, *postings: object, verified: bool = True) -> None:
        self.collector.boards["alpha"] = FetchedBoard(
            postings=list(postings), completeness_verified=verified
        )

    def run(self) -> CompanyCrawlSummary:
        summary = self.service.run(CONFIG)
        self.last = summary
        [company] = summary.companies
        return company

    def seed(self, *ids: int) -> None:
        """A first run storing ``ids``, then time moves on."""
        self.serve(*(posting(i) for i in ids))
        assert self.run().counters.new == len(ids)
        self.clock.advance(timedelta(hours=1))

    def row(self, job_id: int) -> FakeJobRow:
        return self.db.state.jobs[(GREENHOUSE, str(job_id))]

    def active(self) -> list[int]:
        return sorted(
            int(job_id)
            for (_, job_id), row in self.db.state.jobs.items()
            if row.is_active
        )

    def company_calls(self) -> list[str]:
        return [
            t.calls for t in self.db.transactions if t.operation == "persist_company"
        ][-1]


def records(
    caplog: pytest.LogCaptureFixture, operation: str
) -> list[logging.LogRecord]:
    return [
        r
        for r in caplog.records
        if r.name == LOGGER_NAME and getattr(r, "operation", None) == operation
    ]


@pytest.fixture
def h() -> Harness:
    return Harness()


# --- deactivation ----------------------------------------------------------------


def test_a_job_absent_from_a_verified_valid_crawl_is_deactivated(
    h: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    h.seed(1, 2, 3)
    h.serve(posting(1), posting(2))
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)

    company = h.run()

    assert h.active() == [1, 2]
    assert h.row(3).deactivated_at == T0 + timedelta(hours=1)
    assert h.row(3).last_seen_at == T0, "observation history is untouched"
    assert company.status is CompanyCrawlStatus.SUCCESS
    assert company.counters == JobCounters(fetched=2, existing=2, deactivated=1)
    assert company.completeness_verified is True
    [result] = h.db.state.results[-1:]
    assert result.counters.deactivated == 1
    assert result.completeness_verified is True
    assert h.last.totals.deactivated == 1
    [record] = records(caplog, "deactivate_jobs")
    assert record.levelno == logging.INFO
    assert (record.absent, record.deactivated, record.previously_active) == (1, 1, 3)  # type: ignore[attr-defined]
    [completion] = records(caplog, "crawl_run")
    assert completion.deactivated == 1  # type: ignore[attr-defined]


def test_count_active_runs_before_any_upsert_and_deactivation_before_the_record(
    h: Harness,
) -> None:
    h.seed(1, 2, 3)
    h.serve(posting(1), posting(2))

    h.run()

    assert h.company_calls() == [
        "count_active",
        "upsert",
        "upsert",
        "count_absent",
        "deactivate_absent",
        "record_company",
    ]


def test_a_first_crawl_has_nothing_to_deactivate_and_logs_nothing(
    h: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)
    h.serve(posting(1), verified=False)

    company = h.run()

    assert company.counters.deactivated == 0
    assert records(caplog, "deactivate_jobs") == []
    assert h.company_calls() == ["count_active", "upsert", "record_company"]


def test_an_unchanged_rerun_deactivates_nothing(h: Harness) -> None:
    h.seed(1, 2)
    h.serve(posting(1), posting(2))

    company = h.run()

    assert company.counters == JobCounters(fetched=2, existing=2)
    assert h.active() == [1, 2]
    assert "deactivate_absent" not in h.company_calls()


def test_a_deactivated_job_that_reappears_is_active_again(h: Harness) -> None:
    h.seed(1, 2, 3)
    h.serve(posting(1), posting(2))
    h.run()
    h.clock.advance(timedelta(hours=1))
    h.serve(posting(1), posting(2), posting(3))

    company = h.run()

    assert h.active() == [1, 2, 3]
    assert h.row(3).deactivated_at is None
    assert company.counters == JobCounters(fetched=3, existing=3)


# --- guards: each skip is logged, not a failure ------------------------------------


def assert_skipped(
    h: Harness,
    caplog: pytest.LogCaptureFixture,
    company: CompanyCrawlSummary,
    reason: str,
    *,
    level: int = logging.INFO,
) -> logging.LogRecord:
    assert company.status is CompanyCrawlStatus.SUCCESS
    assert h.last.status is CrawlRunStatus.SUCCESS
    assert company.counters.deactivated == 0
    assert "deactivate_absent" not in h.company_calls()
    [record] = records(caplog, "deactivate_jobs")
    assert record.levelno == level
    assert record.deactivation_skipped == reason  # type: ignore[attr-defined]
    assert record.board_token == "alpha"  # type: ignore[attr-defined]
    return record


def test_unverified_completeness_skips(
    h: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    h.seed(1, 2, 3)
    h.serve(posting(1), verified=False)
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)

    company = h.run()

    record = assert_skipped(h, caplog, company, "completeness_unverified")
    assert record.previously_active == 3  # type: ignore[attr-defined]
    assert not hasattr(record, "absent"), "K is not computed before a precheck fails"
    assert h.active() == [1, 2, 3]
    assert company.completeness_verified is False
    assert h.db.state.results[-1].completeness_verified is False


def test_an_empty_board_skips(h: Harness, caplog: pytest.LogCaptureFixture) -> None:
    h.seed(1, 2)
    h.serve()
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)

    company = h.run()

    assert_skipped(h, caplog, company, "empty_board")
    assert h.active() == [1, 2]


def test_an_invalid_posting_without_an_id_skips(
    h: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    h.seed(1, 2, 3)
    h.serve(posting(1), {"no": "id"})
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)

    company = h.run()

    assert_skipped(h, caplog, company, "invalid_posting_without_id")
    assert h.active() == [1, 2, 3]
    assert company.counters.invalid == 1


def test_no_valid_observation_skips(
    h: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    h.seed(1, 2, 3)
    h.serve(posting(1, invalid=True), posting(2, invalid=True))
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)

    company = h.run()

    assert_skipped(h, caplog, company, "no_valid_observations")
    assert h.active() == [1, 2, 3]


def test_an_invalid_posting_with_a_known_id_protects_its_job(h: Harness) -> None:
    h.seed(1, 2, 3)
    h.serve(posting(1), posting(3, invalid=True))

    company = h.run()

    assert h.active() == [1, 3]
    assert company.counters == JobCounters(
        fetched=2, existing=1, invalid=1, deactivated=1
    )


class PaddedIdNormalizer(FakeNormalizer):
    """Reports an invalid posting's id with whitespace: not canonical."""

    def normalize(
        self, posting: object, *, company: str, observed_at: datetime
    ) -> NormalizedJob:
        if isinstance(posting, dict) and posting.get("padded"):
            raise NormalizationError(
                source=GREENHOUSE, source_job_id=f" {posting['id']}", reason="bad"
            )
        return super().normalize(posting, company=company, observed_at=observed_at)


def test_a_non_canonical_reported_id_counts_as_unknown(
    caplog: pytest.LogCaptureFixture,
) -> None:
    h = Harness(PaddedIdNormalizer())
    h.seed(1, 2, 3)
    h.serve(posting(1), posting(3, padded=True))
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)

    company = h.run()

    assert_skipped(h, caplog, company, "invalid_posting_without_id")
    assert h.active() == [1, 2, 3]


# --- the large-disappearance guard (D6') -------------------------------------------


def present(ids: Sequence[int]) -> list[object]:
    return [posting(i) for i in ids]


def test_more_than_half_and_at_least_five_skips_with_a_warning(
    h: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    h.seed(*range(1, 11))
    h.serve(*present(range(1, 5)))  # P=10, K=6
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)

    company = h.run()

    record = assert_skipped(
        h, caplog, company, "large_disappearance", level=logging.WARNING
    )
    assert (record.absent, record.previously_active) == (6, 10)  # type: ignore[attr-defined]
    assert h.active() == list(range(1, 11))
    assert company.counters == JobCounters(fetched=4, existing=4)
    assert h.row(5).last_seen_at == T0, "absent jobs are not touched"
    assert h.row(1).last_seen_at == T0 + timedelta(hours=1), "upserts stand"


@pytest.mark.parametrize(
    ("previously_active", "kept", "deactivated"),
    [
        (10, 5, 5),  # exactly half: not more than half
        (9, 5, 4),
        (8, 4, 4),
    ],
)
def test_half_or_less_is_deactivated(
    h: Harness, previously_active: int, kept: int, deactivated: int
) -> None:
    h.seed(*range(1, previously_active + 1))
    h.serve(*present(range(1, kept + 1)))

    company = h.run()

    assert company.counters.deactivated == deactivated
    assert h.active() == list(range(1, kept + 1))


def test_fewer_than_five_absent_is_deactivated_even_if_all(h: Harness) -> None:
    h.seed(1, 2, 3, 4)
    h.serve(posting(99))  # P=4, K=4: below the five-job minimum

    company = h.run()

    assert company.counters == JobCounters(fetched=1, new=1, deactivated=4)
    assert h.active() == [99]


def test_exactly_five_of_five_is_skipped(
    h: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    h.seed(1, 2, 3, 4, 5)
    h.serve(posting(99))
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)

    company = h.run()

    assert_skipped(h, caplog, company, "large_disappearance", level=logging.WARNING)
    assert h.active() == [1, 2, 3, 4, 5, 99]


def test_a_later_crawl_reevaluates_after_a_skip(h: Harness) -> None:
    h.seed(*range(1, 11))
    h.serve(*present(range(1, 5)))
    assert h.run().counters.deactivated == 0  # K=6 of 10: skipped
    h.clock.advance(timedelta(hours=1))
    h.serve(*present(range(1, 7)))

    company = h.run()  # K=4 of 10: deactivated

    assert company.counters.deactivated == 4
    assert h.active() == list(range(1, 7))


def test_previously_active_counts_stored_jobs_not_fetched_postings(h: Harness) -> None:
    # Many new postings do not dilute the share of stored jobs that vanished.
    h.seed(*range(1, 7))
    h.serve(posting(1), *present(range(100, 120)))  # P=6, K=5, fetched=21

    company = h.run()

    assert company.counters.deactivated == 0
    assert h.active() == [*range(1, 7), *range(100, 120)]


# --- transactions -----------------------------------------------------------------


@pytest.mark.parametrize(
    "method", ["count_active", "count_absent", "deactivate_absent"]
)
def test_a_database_failure_while_deactivating_fails_the_whole_company(
    h: Harness, method: str
) -> None:
    h.seed(1, 2, 3)
    before = {
        key: (row.is_active, row.last_seen_at) for key, row in h.db.state.jobs.items()
    }
    h.serve(posting(1), posting(2), posting(4))
    h.db.fail_on(method, operation="persist_company")

    company = h.run()

    assert company.status is CompanyCrawlStatus.FAILED
    assert company.counters == JobCounters()
    assert company.completeness_verified is None
    assert h.db.state.results[-1].completeness_verified is None
    after = {
        key: (row.is_active, row.last_seen_at) for key, row in h.db.state.jobs.items()
    }
    assert after == before, "upserts and deactivation roll back together"


def test_a_failed_crawl_never_deactivates(h: Harness) -> None:
    h.seed(1, 2, 3)
    h.collector.boards["alpha"] = CollectorError(
        source=GREENHOUSE, company="alpha", operation="fetch", message="HTTP 500"
    )

    company = h.run()

    assert company.status is CompanyCrawlStatus.FAILED
    assert h.active() == [1, 2, 3]
