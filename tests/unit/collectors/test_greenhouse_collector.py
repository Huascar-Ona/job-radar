"""GreenhouseCollector: transport and provider retrieval only.

Every test drives a stubbed transport. Nothing here touches the network, so
the suite does not depend on Greenhouse being up, or on any board still
existing (ADR-0010's reasoning applied to HTTP rather than to the database).
"""

import json
import traceback
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

    jobs = collector.fetch_jobs(board_token=BOARD).postings

    assert list(jobs) == [POSTING]


def test_returns_payloads_without_normalizing_them() -> None:
    # The collector must not convert to the domain model; that is the
    # normalizer's job (ADR-0016).
    collector, _ = collector_for(responding(200, json_body=board(POSTING)))

    [job] = collector.fetch_jobs(board_token=BOARD).postings

    assert isinstance(job, dict)
    assert job["id"] == 8653419002, "provider field names survive untouched"
    assert job["content"] == "&lt;p&gt;We are hiring.&lt;/p&gt;", "HTML still escaped"
    assert "source_job_id" not in job
    assert "observed_at" not in job


def test_empty_board_is_a_valid_result() -> None:
    # A board with no open roles is not a failure.
    collector, _ = collector_for(responding(200, json_body=board()))

    assert list(collector.fetch_jobs(board_token=BOARD).postings) == []


def test_unknown_provider_fields_are_tolerated() -> None:
    # Greenhouse adds fields; rejecting them would break crawling for a change
    # that does not affect us (ADR-0016).
    posting = {**POSTING, "brand_new_field": {"nested": [1, 2]}}
    collector, _ = collector_for(responding(200, json_body=board(posting)))

    [job] = collector.fetch_jobs(board_token=BOARD).postings

    # The loose return type forces the caller to narrow, which is the point:
    # the collector promises a list, not a list of valid postings (ADR-0016).
    assert isinstance(job, dict)
    assert job["brand_new_field"] == {"nested": [1, 2]}


def test_extra_envelope_keys_are_tolerated() -> None:
    body = {**board(POSTING), "unexpected": 1}
    collector, _ = collector_for(responding(200, json_body=body))

    assert len(collector.fetch_jobs(board_token=BOARD).postings) == 1


def test_non_object_postings_are_left_for_the_normalizer() -> None:
    # ADR-0016 scopes the collector's envelope check to `jobs` being a list.
    # Whether an element is convertible is a per-posting question.
    collector, _ = collector_for(
        responding(200, json_body=board(POSTING, "not-an-object"))
    )

    assert len(collector.fetch_jobs(board_token=BOARD).postings) == 2


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


# --- completeness: meta.total (ADR-0016 addendum) ---------------------------


def assert_decode_failure(body: object) -> CollectorError:
    collector, _ = collector_for(responding(200, json_body=body))

    with pytest.raises(CollectorError) as caught:
        collector.fetch_jobs(board_token=BOARD)

    error = caught.value
    assert error.operation == "decode"
    assert error.source == "greenhouse"
    assert error.company == BOARD
    assert error.log_context()["operation"] == "decode"
    return error


def test_matching_total_is_accepted() -> None:
    other = {**POSTING, "id": 8653419003}
    body = {"jobs": [POSTING, other], "meta": {"total": 2}}
    collector, _ = collector_for(responding(200, json_body=body))

    assert list(collector.fetch_jobs(board_token=BOARD).postings) == [POSTING, other]


def test_empty_board_with_total_zero_is_accepted() -> None:
    body = {"jobs": [], "meta": {"total": 0}}
    collector, _ = collector_for(responding(200, json_body=body))

    assert list(collector.fetch_jobs(board_token=BOARD).postings) == []


def test_total_greater_than_jobs_is_a_decode_failure() -> None:
    # The board says it has more than it sent: a truncated response must not
    # be recorded as a successful crawl.
    error = assert_decode_failure({"jobs": [POSTING], "meta": {"total": 2}})

    assert "2" in error.message
    assert "1" in error.message


def test_total_smaller_than_jobs_is_a_decode_failure() -> None:
    error = assert_decode_failure({"jobs": [POSTING, POSTING], "meta": {"total": 1}})

    assert "total" in error.message


def test_total_mismatch_on_an_empty_board_is_a_decode_failure() -> None:
    assert_decode_failure({"jobs": [], "meta": {"total": 3}})


@pytest.mark.parametrize(
    "total",
    ["1", 1.0, True, False, [1], {"value": 1}],
    ids=["string", "float", "true", "false", "list", "object"],
)
def test_total_that_is_not_a_strict_integer_is_a_decode_failure(
    total: object,
) -> None:
    # 1.0 == 1 and True == 1 in Python; neither is a JSON integer count.
    error = assert_decode_failure({"jobs": [POSTING], "meta": {"total": total}})

    assert "integer" in error.message


def test_float_total_is_rejected_even_when_parsed_from_the_wire() -> None:
    # Guards against relying on the stub's own serialization: "1.0" on the
    # wire decodes to a Python float.
    collector, _ = collector_for(
        responding(200, text='{"jobs": [{"id": 1}], "meta": {"total": 1.0}}')
    )

    with pytest.raises(CollectorError) as caught:
        collector.fetch_jobs(board_token=BOARD)

    assert caught.value.operation == "decode"


def test_missing_meta_is_accepted_unverified() -> None:
    collector, _ = collector_for(responding(200, json_body={"jobs": [POSTING]}))

    assert list(collector.fetch_jobs(board_token=BOARD).postings) == [POSTING]


def test_null_meta_is_accepted_unverified() -> None:
    body = {"jobs": [POSTING], "meta": None}
    collector, _ = collector_for(responding(200, json_body=body))

    assert list(collector.fetch_jobs(board_token=BOARD).postings) == [POSTING]


@pytest.mark.parametrize(
    "meta",
    [[], [{"total": 1}], "total", 1, True],
    ids=["empty-list", "list", "string", "number", "boolean"],
)
def test_meta_that_is_not_an_object_is_a_decode_failure(meta: object) -> None:
    error = assert_decode_failure({"jobs": [POSTING], "meta": meta})

    assert "meta" in error.message


def test_meta_without_total_is_accepted_unverified() -> None:
    body = {"jobs": [POSTING], "meta": {"other": "x"}}
    collector, _ = collector_for(responding(200, json_body=body))

    assert list(collector.fetch_jobs(board_token=BOARD).postings) == [POSTING]


def test_null_total_is_accepted_unverified() -> None:
    body = {"jobs": [POSTING], "meta": {"total": None}}
    collector, _ = collector_for(responding(200, json_body=body))

    assert list(collector.fetch_jobs(board_token=BOARD).postings) == [POSTING]


@pytest.mark.parametrize(
    ("body", "verified"),
    [
        ({"jobs": [POSTING], "meta": {"total": 1}}, True),
        ({"jobs": [], "meta": {"total": 0}}, True),
        ({"jobs": [POSTING]}, False),
        ({"jobs": [POSTING], "meta": None}, False),
        ({"jobs": [POSTING], "meta": {"other": "x"}}, False),
        ({"jobs": [POSTING], "meta": {"total": None}}, False),
    ],
    ids=[
        "matching-total",
        "empty-total-zero",
        "no-meta",
        "null-meta",
        "no-total",
        "null-total",
    ],
)
def test_completeness_is_verified_only_by_a_matching_total(
    body: dict[str, object], verified: bool
) -> None:
    # ADR-0017: deactivation reasons about absence only from a verified fetch.
    collector, _ = collector_for(responding(200, json_body=body))

    assert collector.fetch_jobs(board_token=BOARD).completeness_verified is verified


def test_total_counts_malformed_postings_too() -> None:
    # meta.total is compared with the length of `jobs`, not with the number
    # of convertible postings: element shape stays the normalizer's concern.
    body = {"jobs": [POSTING, "not-an-object", None], "meta": {"total": 3}}
    collector, _ = collector_for(responding(200, json_body=body))

    assert len(collector.fetch_jobs(board_token=BOARD).postings) == 3


@pytest.mark.parametrize(
    "jobs", [{}, "not-a-list", 42, None], ids=["object", "string", "number", "null"]
)
def test_jobs_type_is_checked_before_meta(jobs: object) -> None:
    # The existing `jobs` failure is unchanged by a valid or invalid meta.
    error = assert_decode_failure({"jobs": jobs, "meta": {"total": "bad"}})

    assert "jobs" in error.message


# --- safe error messages (SPEC, "Safe error messages") ----------------------

SECRET = "SECRET-MARKER-c41b"


def failure_of(handler: Callable[[httpx.Request], httpx.Response]) -> CollectorError:
    collector, _ = collector_for(handler)

    with pytest.raises(CollectorError) as caught:
        collector.fetch_jobs(board_token=BOARD)

    return caught.value


def assert_safe(error: CollectorError) -> None:
    """No marker anywhere a caller could read it, and no exception chain."""
    assert error.__cause__ is None
    assert error.__context__ is None
    for text in (
        error.message,
        str(error),
        repr(error),
        repr(error.args),
        repr(error.log_context()),
        "".join(traceback.format_exception(error)),
    ):
        assert SECRET not in text, text


@pytest.mark.parametrize("status", [404, 429, 500, 503])
@pytest.mark.parametrize(
    "body",
    [
        f"<html><body>{SECRET}</body></html>",
        f'{{"error": "{SECRET}"}}',
        SECRET * 1_000,
    ],
    ids=["html", "json", "long"],
)
def test_an_http_failure_reports_only_its_status(status: int, body: str) -> None:
    error = failure_of(responding(status, text=body))

    assert error.operation == "fetch"
    assert error.message == f"HTTP {status}"
    assert_safe(error)


def test_a_timeout_has_a_fixed_message() -> None:
    error = failure_of(raising(httpx.ReadTimeout(f"timed out reading {SECRET}")))

    assert error.operation == "fetch"
    assert error.message == "request timeout"
    assert_safe(error)


@pytest.mark.parametrize(
    "exc",
    [
        httpx.ConnectError(f"[Errno 8] cannot resolve {SECRET}"),
        httpx.RemoteProtocolError(f"peer closed: {SECRET}"),
        httpx.ReadError(SECRET),
    ],
    ids=lambda exc: type(exc).__name__,
)
def test_a_transport_failure_reports_only_its_category(exc: httpx.HTTPError) -> None:
    error = failure_of(raising(exc))

    assert error.operation == "fetch"
    assert error.message == f"transport error ({type(exc).__name__})"
    assert_safe(error)


def test_invalid_json_has_a_fixed_message() -> None:
    error = failure_of(responding(200, text=f'{{"jobs": [{SECRET}]}}'))

    assert error.operation == "decode"
    assert error.message == "response is not valid JSON"
    assert_safe(error)


def test_a_decoder_error_quoting_the_body_is_not_kept(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The standard decoder quotes positions only; one that quoted the body
    # must not get through either.
    def decode(_self: httpx.Response, **_: object) -> object:
        raise json.JSONDecodeError(f"unexpected {SECRET}", SECRET, 0)

    monkeypatch.setattr(httpx.Response, "json", decode)

    error = failure_of(responding(200, json_body=board()))

    assert error.message == "response is not valid JSON"
    assert_safe(error)


def test_undecodable_bytes_have_a_fixed_message() -> None:
    error = failure_of(
        lambda _: httpx.Response(200, content=b'{"jobs": "\xff' + SECRET.encode())
    )

    assert error.message == "response is not valid JSON"
    assert_safe(error)


def test_an_absurd_total_is_not_quoted() -> None:
    error = assert_decode_failure({"jobs": [], "meta": {"total": 10**200}})

    assert "out of range" in error.message
    assert len(error.message) < 100


def test_decode_failures_name_types_not_values() -> None:
    error = assert_decode_failure({"jobs": SECRET})

    assert error.message == "'jobs' is not a list, got str"
    assert_safe(error)


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


def test_create_client_uses_an_injected_transport_with_production_settings() -> None:
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"jobs": [], "meta": {"total": 0}})

    with create_client(
        timeout_seconds=2.5, transport=httpx.MockTransport(handle)
    ) as client:
        assert GreenhouseCollector(client).fetch_jobs(board_token=BOARD).postings == []
        assert client.timeout.read == 2.5

    [request] = seen
    assert "job-radar" in request.headers["user-agent"].lower()


def test_default_client_has_no_infinite_timeout() -> None:
    # Guards the guard: httpx defaults to 5s, but an explicit None anywhere
    # would mean "wait forever" and silently defeat failure isolation.
    with create_client(timeout_seconds=3.0) as client:
        assert client.timeout.read is not None
