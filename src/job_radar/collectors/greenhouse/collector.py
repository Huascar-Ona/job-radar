"""Retrieving postings from the Greenhouse Job Board API.

Transport only. This module knows URLs, status codes and JSON; it does not
know what a job is. Everything it returns is exactly what Greenhouse sent
(ADR-0016).

The endpoint and its lack of authentication are documented. What is *not*
documented shapes the code as much as what is: there is no stated rate limit
and no `Retry-After` header, so nothing is retried here (ADR-0016); error
bodies are not reliably JSON, so status is classified before any body is
parsed; and `content=true` is matched literally, so the parameter is sent as
that exact string.

Error messages are safe by construction (SPEC, "Safe error messages"). They
are assembled only from fixed text, the HTTP status code, Python type names
and integer counts. Response bodies, exception text and source values are
never quoted, and no ``CollectorError`` carries the exception that caused it:
each is raised after its ``except`` block has ended, so it has neither a
``__cause__`` nor a ``__context__``.
"""

import json
import re
from typing import Final

import httpx

from job_radar.collectors.base import FetchedBoard
from job_radar.domain.errors import CollectorError

BOARDS_API_ROOT: Final = "https://boards-api.greenhouse.io/v1"
SOURCE: Final = "greenhouse"

#: Identifies the crawler to the provider. Politeness, and it gives Greenhouse
#: someone to contact instead of a silent block.
USER_AGENT: Final = "job-radar/0.1 (+https://github.com/HuascarOna/job-radar)"

#: Characters that end a path segment. A token carrying one of these does not
#: land where the URL says it does: "a?x=1" truncates the path into a query,
#: "a#f" into a fragment, and "a/b" adds a segment.
_PATH_DELIMITERS: Final = frozenset("/?#")

#: Segments that RFC 3986 resolves away rather than keeping: "boards/../jobs"
#: becomes "/v1/jobs", a different endpoint entirely.
_RELATIVE_SEGMENTS: Final = frozenset({".", ".."})

#: An exception class name quoted in a message: a plain identifier, bounded.
_CATEGORY: Final = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")

#: Largest ``meta.total`` quoted verbatim. Any JSON integer parses, so a
#: hostile one could otherwise be thousands of digits long.
_MAX_QUOTED_TOTAL: Final = 10**9


def create_client(
    *, timeout_seconds: float, transport: httpx.BaseTransport | None = None
) -> httpx.Client:
    """Build an HTTP client with a bounded timeout and an identifying agent.

    The timeout covers connect, read, write and pool: a board that accepts the
    connection and then says nothing must cost one timeout, not the run
    (ADR-0009).

    ``transport`` is for tests that stub HTTP while keeping the production
    timeout and headers. ``None``, the default, is httpx's own network
    transport.
    """
    if timeout_seconds <= 0:
        msg = f"timeout must be positive, got {timeout_seconds!r}"
        raise ValueError(msg)
    # Redirects are left at the httpx default (not followed). Nothing
    # documented or observed says the Board API redirects, and `absolute_url`
    # pointing at customer domains is response data the collector never
    # navigates to.
    return httpx.Client(
        timeout=httpx.Timeout(timeout_seconds),
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        transport=transport,
    )


class GreenhouseCollector:
    """Fetches the current postings of one Greenhouse board.

    The client is injected rather than built here, so its lifecycle belongs to
    the caller and tests can supply a stubbed transport.
    """

    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    def fetch_jobs(self, *, board_token: str) -> FetchedBoard:
        """Return the board's postings, exactly as Greenhouse sent them.

        Completeness is verified when ``meta.total`` is present and equals
        the number of postings; absent, it is unverified (ADR-0017).

        Raises:
            CollectorError: the board could not be retrieved, or what came
                back was not a Job Board API response.
        """
        if (problem := _path_segment_problem(board_token)) is not None:
            raise self._failure(
                board_token, "validate", f"unusable board token ({problem})"
            )

        response = self._get(board_token)
        if response.status_code != httpx.codes.OK:
            # The status code only: an error body is provider-controlled
            # content, not a diagnosis (SPEC, "Safe error messages").
            raise self._failure(
                board_token, "fetch", f"HTTP {int(response.status_code)}"
            )
        return self._postings(board_token, response)

    # -- transport -----------------------------------------------------------

    def _get(self, board_token: str) -> httpx.Response:
        url = f"{BOARDS_API_ROOT}/boards/{board_token}/jobs"
        failure: CollectorError
        try:
            # `content=true` is matched literally by Greenhouse: any other
            # value silently omits content, departments and offices (SPEC).
            #
            # The headers are sent per request as well as being client
            # defaults: ADR-0016 puts the identifying User-Agent on the
            # collector, so the guarantee must not depend on how the injected
            # client happened to be built.
            return self._client.get(
                url,
                params={"content": "true"},
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            )
        except httpx.TimeoutException:
            failure = self._failure(board_token, "fetch", "request timeout")
        except httpx.HTTPError as exc:
            failure = self._failure(
                board_token, "fetch", f"transport error ({_category(exc)})"
            )
        # Raised outside the except block: the httpx exception, whose text may
        # carry anything, is neither the cause nor the context.
        raise failure

    # -- envelope ------------------------------------------------------------

    def _postings(self, board_token: str, response: httpx.Response) -> FetchedBoard:
        body: object
        try:
            body = response.json()
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
            invalid_json = True
        else:
            invalid_json = False
        # Decoder messages quote positions and bytes of the body; none of it
        # is kept, and the decoder error is not chained.
        if invalid_json:
            raise self._failure(board_token, "decode", "response is not valid JSON")

        if not isinstance(body, dict):
            raise self._failure(
                board_token,
                "decode",
                f"response body is not an object, got {type(body).__name__}",
            )
        if "jobs" not in body:
            raise self._failure(
                board_token, "decode", "response body has no 'jobs' key"
            )

        jobs: object = body["jobs"]
        if not isinstance(jobs, list):
            raise self._failure(
                board_token,
                "decode",
                f"'jobs' is not a list, got {type(jobs).__name__}",
            )
        # Element shape is deliberately not checked: one unconvertible posting
        # must not fail an otherwise good board (ADR-0016).
        verified = self._check_completeness(board_token, body.get("meta"), len(jobs))
        return FetchedBoard(postings=jobs, completeness_verified=verified)

    def _check_completeness(
        self, board_token: str, meta: object, job_count: int
    ) -> bool:
        """Enforce ``meta.total`` when Greenhouse reports it (ADR-0016 addendum).

        ``meta.total`` is observed but undocumented, so its absence is
        tolerated and completeness is then simply unverified. When present it
        must agree with ``jobs``: a board that disagrees with its own count is
        a failed crawl, never a silently truncated successful one. No
        pagination is attempted.

        Returns whether completeness was verified.
        """
        if meta is None:
            return False
        if not isinstance(meta, dict):
            raise self._failure(
                board_token,
                "decode",
                f"'meta' is not an object, got {type(meta).__name__}",
            )
        total: object = meta.get("total")
        if total is None:
            return False
        # bool is an int subclass in Python, but JSON true is not a count.
        if isinstance(total, bool) or not isinstance(total, int):
            raise self._failure(
                board_token,
                "decode",
                f"'meta.total' is not an integer, got {type(total).__name__}",
            )
        if total != job_count:
            quoted = str(total) if abs(total) <= _MAX_QUOTED_TOTAL else "out of range"
            raise self._failure(
                board_token,
                "decode",
                f"'meta.total' is {quoted} but 'jobs' has {job_count} entries",
            )
        return True

    def _failure(
        self, board_token: str, operation: str, message: str
    ) -> CollectorError:
        return CollectorError(
            source=SOURCE, company=board_token, operation=operation, message=message
        )


def _path_segment_problem(board_token: str) -> str | None:
    """Why ``board_token`` cannot be used as a URL path segment, if it cannot.

    Structural only. This says nothing about which tokens Greenhouse considers
    valid — that is the provider's business, and no such rule is documented.
    It rejects only tokens that would send the request somewhere other than
    where the URL appears to point. The token is never trimmed, lowercased or
    otherwise rewritten: a token that needs fixing is a configuration error,
    and silently repairing it would hide the mistake.
    """
    if not board_token:
        return "empty"
    if any(character.isspace() for character in board_token):
        return "contains whitespace"
    if delimiters := sorted(_PATH_DELIMITERS.intersection(board_token)):
        return f"contains path delimiter {''.join(delimiters)!r}"
    if board_token in _RELATIVE_SEGMENTS:
        return "is a relative path segment"
    return None


def _category(exc: BaseException) -> str:
    """The exception's class name, if it is a plain identifier."""
    name = type(exc).__name__
    return name if _CATEGORY.fullmatch(name) else "HTTPError"
