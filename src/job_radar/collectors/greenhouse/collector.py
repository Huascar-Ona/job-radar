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
"""

import json
from collections.abc import Sequence
from typing import Final

import httpx

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

#: Enough of a failing body to diagnose with, not enough to paste an HTML page
#: into a log line.
_ERROR_EXCERPT_CHARS: Final = 200


def create_client(*, timeout_seconds: float) -> httpx.Client:
    """Build an HTTP client with a bounded timeout and an identifying agent.

    The timeout covers connect, read, write and pool: a board that accepts the
    connection and then says nothing must cost one timeout, not the run
    (ADR-0009).
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
    )


class GreenhouseCollector:
    """Fetches the current postings of one Greenhouse board.

    The client is injected rather than built here, so its lifecycle belongs to
    the caller and tests can supply a stubbed transport.
    """

    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    def fetch_jobs(self, *, board_token: str) -> Sequence[object]:
        """Return the board's postings, exactly as Greenhouse sent them.

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
            raise self._failure(
                board_token,
                "fetch",
                f"HTTP {response.status_code}: {_excerpt(response)}",
            )
        return self._postings(board_token, response)

    # -- transport -----------------------------------------------------------

    def _get(self, board_token: str) -> httpx.Response:
        url = f"{BOARDS_API_ROOT}/boards/{board_token}/jobs"
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
        except httpx.TimeoutException as exc:
            raise self._failure(
                board_token, "fetch", f"request timeout: {exc}"
            ) from exc
        except httpx.HTTPError as exc:
            raise self._failure(
                board_token, "fetch", f"{type(exc).__name__}: {exc}"
            ) from exc

    # -- envelope ------------------------------------------------------------

    def _postings(self, board_token: str, response: httpx.Response) -> Sequence[object]:
        try:
            body: object = response.json()
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
            raise self._failure(
                board_token, "decode", f"response is not valid JSON: {exc}"
            ) from exc

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
        return jobs

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


def _excerpt(response: httpx.Response) -> str:
    """A short, safe rendering of a failing body, JSON or not."""
    try:
        text = response.text.strip()
    except (UnicodeDecodeError, httpx.ResponseNotRead):  # pragma: no cover
        return "<unreadable body>"
    if not text:
        return "<empty body>"
    collapsed = " ".join(text.split())
    if len(collapsed) <= _ERROR_EXCERPT_CHARS:
        return collapsed
    return collapsed[:_ERROR_EXCERPT_CHARS] + "..."
