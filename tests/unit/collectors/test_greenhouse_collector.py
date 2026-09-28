"""GreenhouseCollector: transport and provider retrieval only.

Every test drives a stubbed transport. Nothing here touches the network, so
the suite does not depend on Greenhouse being up, or on any board still
existing (ADR-0010's reasoning applied to HTTP rather than to the database).
"""

from collections.abc import Callable

import httpx
import pytest

from job_radar.collectors.greenhouse.collector import (
    BOARDS_API_ROOT,
    GreenhouseCollector,
    create_client,
)
from job_radar.domain.errors import CollectorError, JobRadarError

BOARD = "examplecorp"

POSTING = {
    "id": 8653419002,
    "internal_job_id": 6455838002,
    "title": "Senior Backend Engineer",
    "updated_at": "2026-08-21T13:02:33-04:00",
    "first_published": "2026-07-28T09:47:45-04:00",
    "location": {"name": "Remote - Mexico"},
    "absolute_url": "https://careers.examplecorp.com/jobs/8653419002",
    "content": "&lt;p&gt;We are hiring.&lt;/p&gt;",
    "departments": [
        {"id": 1, "name": "Engineering", "child_ids": [], "parent_id": None}
    ],
    "offices": [],
    "metadata": None,
}


def collector_for(
    handler: Callable[[httpx.Request], httpx.Response],
) -> tuple[GreenhouseCollector, list[httpx.Request]]:
    """A collector whose transport is a stub, plus the requests it received."""
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    client = httpx.Client(transport=httpx.MockTransport(record))
    return GreenhouseCollector(client), seen


def responding(
    status: int, *, json_body: object = None, text: str | None = None
) -> Callable[[httpx.Request], httpx.Response]:
    def handler(_: httpx.Request) -> httpx.Response:
        if text is not None:
            return httpx.Response(status, text=text)
        return httpx.Response(status, json=json_body)

    return handler


def board(*postings: object) -> dict[str, object]:
    return {"jobs": list(postings), "meta": {"total": len(postings)}}


# --- request construction ---------------------------------------------------


def test_requests_the_documented_endpoint() -> None:
    collector, seen = collector_for(responding(200, json_body=board()))

    collector.fetch_jobs(board_token=BOARD)

    assert str(seen[0].url).startswith(f"{BOARDS_API_ROOT}/boards/{BOARD}/jobs")


def test_requests_content_true_literally() -> None:
    # Greenhouse matches the parameter literally: content=1 or content=false
    # return a response without content, departments or offices (SPEC).
    collector, seen = collector_for(responding(200, json_body=board()))

    collector.fetch_jobs(board_token=BOARD)

    assert seen[0].url.params["content"] == "true"


def test_uses_http_get() -> None:
    collector, seen = collector_for(responding(200, json_body=board()))

    collector.fetch_jobs(board_token=BOARD)

    assert seen[0].method == "GET"


def test_sends_an_identifying_user_agent() -> None:
    collector, seen = collector_for(responding(200, json_body=board()))

    collector.fetch_jobs(board_token=BOARD)

    assert "job-radar" in seen[0].headers["user-agent"].lower()


def test_sends_no_authorization() -> None:
    # The public Board API requires no authentication (documented). Sending
    # credentials would be a leak, not a convenience.
    collector, seen = collector_for(responding(200, json_body=board()))

    collector.fetch_jobs(board_token=BOARD)

    assert "authorization" not in {k.lower() for k in seen[0].headers}


@pytest.mark.parametrize(
    "token",
    ["", "   ", "with space", "a\tb", "a/b", "../admin", "a?x=1", "a#frag", ".", ".."],
    ids=[
        "empty",
        "blank",
        "space",
        "tab",
        "slash",
        "traversal",
        "query-delimiter",
        "fragment-delimiter",
        "dot",
        "dot-dot",
    ],
)
def test_rejects_tokens_that_would_move_the_request(token: str) -> None:
    """Each of these sends the request somewhere other than where it appears to.

    Verified against httpx URL building: "a?x=1" becomes
    /v1/boards/a?content=true, and ".." collapses to /v1/jobs.
    """
    collector, seen = collector_for(responding(200, json_body=board()))

    with pytest.raises(CollectorError):
        collector.fetch_jobs(board_token=token)

    assert seen == [], "no request should be made for an unusable token"


@pytest.mark.parametrize(
    "token",
    ["examplecorp", "My-Board_1", "board.name", "caf\u00e9", "a%2Fb", "12345", "a.b.c"],
)
def test_accepts_any_token_that_survives_as_one_segment(token: str) -> None:
    # The check is structural, not an allowlist: mixed case, dots, percent
    # escapes and non-ASCII are Greenhouse's business, not ours. No documented
    # token grammar exists to enforce.
    collector, seen = collector_for(responding(200, json_body=board()))

    collector.fetch_jobs(board_token=token)

    assert len(seen) == 1


def test_a_valid_token_is_never_rewritten() -> None:
    # No trimming, no lowercasing. A token needing repair is a configuration
    # error and must stay visible.
    collector, seen = collector_for(responding(200, json_body=board()))

    collector.fetch_jobs(board_token="My-Board_1")

    assert "/boards/My-Board_1/jobs" in str(seen[0].url)


# --- successful responses ---------------------------------------------------


def test_returns_the_provider_postings() -> None:
    collector, _ = collector_for(responding(200, json_body=board(POSTING)))

    jobs = collector.fetch_jobs(board_token=BOARD)

    assert list(jobs) == [POSTING]


def test_returns_payloads_without_normalizing_them() -> None:
    # The collector must not convert to the domain model; that is the
    # normalizer's job (ADR-0016).
    collector, _ = collector_for(responding(200, json_body=board(POSTING)))

    [job] = collector.fetch_jobs(board_token=BOARD)

    assert isinstance(job, dict)
    assert job["id"] == 8653419002, "provider field names survive untouched"
    assert job["content"] == "&lt;p&gt;We are hiring.&lt;/p&gt;", "HTML still escaped"
    assert "source_job_id" not in job
    assert "observed_at" not in job


def test_empty_board_is_a_valid_result() -> None:
    # A board with no open roles is not a failure.
    collector, _ = collector_for(responding(200, json_body=board()))

    assert list(collector.fetch_jobs(board_token=BOARD)) == []


def test_unknown_provider_fields_are_tolerated() -> None:
    # Greenhouse adds fields; rejecting them would break crawling for a change
    # that does not affect us (ADR-0016).
    posting = {**POSTING, "brand_new_field": {"nested": [1, 2]}}
    collector, _ = collector_for(responding(200, json_body=board(posting)))

    [job] = collector.fetch_jobs(board_token=BOARD)

    # The loose return type forces the caller to narrow, which is the point:
    # the collector promises a list, not a list of valid postings (ADR-0016).
    assert isinstance(job, dict)
    assert job["brand_new_field"] == {"nested": [1, 2]}


def test_extra_envelope_keys_are_tolerated() -> None:
    body = {**board(POSTING), "unexpected": 1}
    collector, _ = collector_for(responding(200, json_body=body))

    assert len(collector.fetch_jobs(board_token=BOARD)) == 1


def test_non_object_postings_are_left_for_the_normalizer() -> None:
    # ADR-0016 scopes the collector's envelope check to `jobs` being a list.
    # Whether an element is convertible is a per-posting question.
    collector, _ = collector_for(
        responding(200, json_body=board(POSTING, "not-an-object"))
    )

    assert len(collector.fetch_jobs(board_token=BOARD)) == 2


# --- transport failures -----------------------------------------------------


def raising(exc: Exception) -> Callable[[httpx.Request], httpx.Response]:
    def handler(_: httpx.Request) -> httpx.Response:
        raise exc

    return handler


def test_timeout_becomes_a_collector_error() -> None:
    collector, _ = collector_for(raising(httpx.ReadTimeout("too slow")))

    with pytest.raises(CollectorError) as caught:
        collector.fetch_jobs(board_token=BOARD)

    assert "timeout" in str(caught.value).lower()


def test_connection_failure_becomes_a_collector_error() -> None:
    collector, _ = collector_for(raising(httpx.ConnectError("no route to host")))

    with pytest.raises(CollectorError):
        collector.fetch_jobs(board_token=BOARD)


def test_transport_failures_do_not_leak_httpx_exceptions() -> None:
    # The ingestion service catches JobRadarError; an httpx exception escaping
    # here would abort the whole run instead of failing one company.
    collector, _ = collector_for(raising(httpx.ConnectError("boom")))

    with pytest.raises(JobRadarError):
        collector.fetch_jobs(board_token=BOARD)


# --- HTTP status failures ---------------------------------------------------


@pytest.mark.parametrize("status", [400, 401, 403, 404, 429, 500, 502, 503])
def test_non_success_status_becomes_a_collector_error(status: int) -> None:
    collector, _ = collector_for(
        responding(status, json_body={"status": status, "error": "Job not found"})
    )

    with pytest.raises(CollectorError) as caught:
        collector.fetch_jobs(board_token=BOARD)

    assert str(status) in str(caught.value)


def test_404_reports_the_board_that_failed() -> None:
    collector, _ = collector_for(responding(404, json_body={"error": "Job not found"}))

    with pytest.raises(CollectorError) as caught:
        collector.fetch_jobs(board_token=BOARD)

    assert caught.value.company == BOARD
    assert caught.value.source == "greenhouse"


def test_error_context_carries_the_four_required_keys() -> None:
    collector, _ = collector_for(responding(500, text="oops"))

    with pytest.raises(CollectorError) as caught:
        collector.fetch_jobs(board_token=BOARD)

    assert set(caught.value.log_context()) == {
        "source",
        "company",
        "operation",
        "error",
    }


@pytest.mark.parametrize(
    "body", ["<!DOCTYPE html><html><body>Not found</body></html>", "", "   "]
)
def test_non_json_error_body_is_handled_safely(body: str) -> None:
    # Observed: a malformed path returns text/html, not JSON (SPEC). Requiring
    # a parseable body would turn a clean 404 into a confusing crash.
    collector, _ = collector_for(responding(404, text=body))

    with pytest.raises(CollectorError) as caught:
        collector.fetch_jobs(board_token=BOARD)

    assert "404" in str(caught.value)


def test_error_message_does_not_embed_a_whole_html_page() -> None:
    collector, _ = collector_for(responding(500, text="x" * 10_000))

    with pytest.raises(CollectorError) as caught:
        collector.fetch_jobs(board_token=BOARD)

    assert len(caught.value.message) < 500


# --- malformed success bodies -----------------------------------------------


def test_malformed_json_becomes_a_collector_error() -> None:
    collector, _ = collector_for(responding(200, text="{not json"))

    with pytest.raises(CollectorError) as caught:
        collector.fetch_jobs(board_token=BOARD)

    assert "json" in str(caught.value).lower()


def test_response_without_jobs_key_becomes_a_collector_error() -> None:
    collector, _ = collector_for(responding(200, json_body={"meta": {"total": 0}}))

    with pytest.raises(CollectorError) as caught:
        collector.fetch_jobs(board_token=BOARD)

    assert "jobs" in str(caught.value)


@pytest.mark.parametrize(
    "jobs", [{}, "not-a-list", 42, None], ids=["object", "string", "number", "null"]
)
def test_jobs_of_the_wrong_type_becomes_a_collector_error(jobs: object) -> None:
    collector, _ = collector_for(responding(200, json_body={"jobs": jobs}))

    with pytest.raises(CollectorError):
        collector.fetch_jobs(board_token=BOARD)


@pytest.mark.parametrize("body", ["[]", '"a string"', "42", "null"])
def test_envelope_that_is_not_an_object_becomes_a_collector_error(body: str) -> None:
    collector, _ = collector_for(responding(200, text=body))

    with pytest.raises(CollectorError):
        collector.fetch_jobs(board_token=BOARD)


def test_envelope_failures_report_the_decode_operation() -> None:
    collector, _ = collector_for(responding(200, json_body={"meta": {}}))

    with pytest.raises(CollectorError) as caught:
        collector.fetch_jobs(board_token=BOARD)

    assert caught.value.operation


# --- client construction ----------------------------------------------------


def test_create_client_sets_a_bounded_timeout() -> None:
    # ADR-0009: a hung board must cost one timeout, not the whole run.
    with create_client(timeout_seconds=7.5) as client:
        assert client.timeout.connect == 7.5
        assert client.timeout.read == 7.5
        assert client.timeout.write == 7.5
        assert client.timeout.pool == 7.5


def test_create_client_does_not_follow_redirects() -> None:
    # Nothing documented or observed says the Board API redirects. Following
    # one silently would mean fetching something we did not ask for.
    with create_client(timeout_seconds=1.0) as client:
        assert client.follow_redirects is False


def test_a_redirect_is_reported_rather_than_followed() -> None:
    collector, _ = collector_for(
        lambda _: httpx.Response(302, headers={"Location": "https://elsewhere.test/x"})
    )

    with pytest.raises(CollectorError) as caught:
        collector.fetch_jobs(board_token=BOARD)

    assert "302" in str(caught.value)


def test_create_client_sets_the_user_agent() -> None:
    with create_client(timeout_seconds=1.0) as client:
        assert "job-radar" in client.headers["user-agent"].lower()


@pytest.mark.parametrize("timeout", [0, -1.0])
def test_create_client_rejects_an_unbounded_timeout(timeout: float) -> None:
    with pytest.raises(ValueError, match="timeout"):
        create_client(timeout_seconds=timeout)


def test_default_client_has_no_infinite_timeout() -> None:
    # Guards the guard: httpx defaults to 5s, but an explicit None anywhere
    # would mean "wait forever" and silently defeat failure isolation.
    with create_client(timeout_seconds=3.0) as client:
        assert client.timeout.read is not None
