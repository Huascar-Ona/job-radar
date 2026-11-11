"""Source failures stay safe all the way through a run.

The real GreenhouseCollector and GreenhouseNormalizer run under the ingestion
service, against a mocked HTTP transport and the in-memory store. Every
failure carries a sensitive marker in the place a real one would: an error
body, a transport exception's text, an undecodable body, a malformed source
value. None of it may reach a log record, a persisted company error, the run
summary or an exception chain (SPEC, "Safe error messages").
"""

import copy
import json
import logging
import traceback
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from job_radar.collectors.greenhouse.collector import GreenhouseCollector
from job_radar.collectors.greenhouse.normalizer import GreenhouseNormalizer
from job_radar.domain.crawl import CompanyCrawlStatus, CrawlRunStatus
from job_radar.domain.job import JobSource
from job_radar.ingestion.service import IngestionService
from job_radar.ingestion.summary import CrawlRunSummary
from tests.support.clock import FixedClock
from tests.support.ingestion_fakes import FakeDatabase, boards

T0 = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
SECRET = "SECRET-MARKER-5d07"

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "greenhouse"
GOOD_POSTING: dict[str, object] = json.loads(
    (FIXTURES / "board_jobs_content.json").read_text(encoding="utf-8")
)["jobs"][0]


def posting(**fields: object) -> dict[str, object]:
    result = copy.deepcopy(GOOD_POSTING)
    result.update(fields)
    return result


def board_body(*postings: object) -> bytes:
    return json.dumps(
        {"jobs": list(postings), "meta": {"total": len(postings)}}
    ).encode()


Handler = Callable[[httpx.Request], httpx.Response]


def raise_(exc: Exception) -> Handler:
    def handler(_: httpx.Request) -> httpx.Response:
        raise exc

    return handler


#: One board per failure, plus one that succeeds.
BOARDS: dict[str, Handler] = {
    "body": lambda _: httpx.Response(503, text=f"<html>{SECRET}</html>"),
    "connect": raise_(httpx.ConnectError(f"cannot resolve {SECRET}")),
    "timeout": raise_(httpx.ReadTimeout(f"no bytes from {SECRET}")),
    "json": lambda _: httpx.Response(200, content=f'{{"jobs": [{SECRET}'.encode()),
    "values": lambda _: httpx.Response(
        200,
        content=board_body(
            posting(id=101, updated_at=SECRET),
            posting(id=102, first_published=f"2026-09-10T00:00:00{SECRET}"),
            posting(id=SECRET),
            posting(id=104),
        ),
    ),
}

EXPECTED_FAILURES = {
    "body": ("fetch", "HTTP 503"),
    "connect": ("fetch", "transport error (ConnectError)"),
    "timeout": ("fetch", "request timeout"),
    "json": ("decode", "response is not valid JSON"),
}


def route(request: httpx.Request) -> httpx.Response:
    # /v1/boards/{token}/jobs
    token = request.url.path.split("/")[3]
    return BOARDS[token](request)


def run(db: FakeDatabase) -> CrawlRunSummary:
    client = httpx.Client(transport=httpx.MockTransport(route))
    service = IngestionService(
        store=db,
        collectors={JobSource.GREENHOUSE: GreenhouseCollector(client)},
        normalizers={JobSource.GREENHOUSE: GreenhouseNormalizer()},
        clock=FixedClock(T0),
    )
    return service.run(boards(*((token, token.title()) for token in BOARDS)))


def all_log_text(caplog: pytest.LogCaptureFixture) -> list[str]:
    texts = [caplog.text]
    for record in caplog.records:
        texts.append(record.getMessage())
        texts.extend(repr(value) for value in record.__dict__.values())
        if record.exc_info:
            texts.append("".join(traceback.format_exception(*record.exc_info)))
    return texts


def test_source_failures_never_leak_source_content(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    db = FakeDatabase()

    summary = run(db)

    assert summary.status is CrawlRunStatus.PARTIAL_SUCCESS
    by_token = {c.board_token: c for c in summary.companies}
    for token, (operation, message) in EXPECTED_FAILURES.items():
        company = by_token[token]
        assert company.status is CompanyCrawlStatus.FAILED, token
        assert company.error_type == "CollectorError"
        assert company.error_message == message
        [log] = [
            r
            for r in caplog.records
            if r.levelno == logging.ERROR and r.__dict__.get("board_token") == token
        ]
        assert log.__dict__["operation"] == operation
        assert log.__dict__["error"] == message
    values = by_token["values"]
    assert values.status is CompanyCrawlStatus.SUCCESS
    assert (values.counters.fetched, values.counters.invalid) == (4, 3)

    persisted = {r.error_message for r in db.state.results if r.error_message}
    assert persisted == {message for _, message in EXPECTED_FAILURES.values()}
    for text in [*all_log_text(caplog), repr(summary), repr(db.state.results)]:
        assert SECRET not in text, text


def test_rejected_postings_are_logged_by_field_and_type(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING)

    run(FakeDatabase())

    reasons = {
        r.__dict__["source_job_id"]: r.__dict__["error"]
        for r in caplog.records
        if r.__dict__.get("operation") == "normalize"
    }
    assert reasons == {
        "101": "updated_at: not an ISO 8601 timestamp",
        "102": "first_published: not an ISO 8601 timestamp",
        None: "id: int_type (got str)",
    }
