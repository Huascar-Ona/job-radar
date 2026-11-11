"""Translating one Greenhouse posting into a NormalizedJob.

Translation only. This module performs no I/O and reads no clock: the posting,
the configured company and the observation instant are all handed to it, so
the same inputs always produce the same observation (ADR-0016).

The field mapping is SPEC's "Greenhouse field mapping". A posting that cannot
be converted raises NormalizationError, which the caller counts as ``invalid``
without failing the rest of the board. A bad *argument* is a caller bug and
raises ValueError instead: wrapping it as NormalizationError would silently
count every posting on every board as invalid.
"""

import copy
import html
import re
from datetime import UTC, datetime
from typing import Final

from pydantic import ValidationError

from job_radar.collectors.greenhouse.schema import GreenhousePosting
from job_radar.domain.errors import NormalizationError
from job_radar.domain.job import JobSource, NormalizedJob

SOURCE: Final = JobSource.GREENHOUSE

#: Upper bound on a rejection reason. A posting carries ~10 KB of content, and
#: none of it belongs in a log line.
_REASON_MAX_CHARS: Final = 300

#: A location segment or validation category quoted in a reason: a plain
#: identifier. Anything else is replaced, never quoted.
_IDENTIFIER: Final = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")


class GreenhouseNormalizer:
    """Converts Greenhouse Board API postings into NormalizedJob observations."""

    def normalize(
        self, posting: object, *, company: str, observed_at: datetime
    ) -> NormalizedJob:
        """Translate one posting.

        Args:
            posting: one element of the Board API's ``jobs`` array, unchanged.
            company: the configured display name of the board (ADR-0007).
                Greenhouse's own ``company_name`` is never used.
            observed_at: the crawl's instant, from the injected clock.

        Raises:
            NormalizationError: this posting cannot be converted.
            ValueError: ``company`` is blank or ``observed_at`` has no
                timezone. These are caller errors, not invalid postings.
        """
        _check_arguments(company=company, observed_at=observed_at)

        # A JSON object decodes to a dict; nothing else is a posting.
        if not isinstance(posting, dict):
            raise _invalid(
                None, f"posting is not an object, got {type(posting).__name__}"
            )
        source_job_id = _known_source_job_id(posting)

        parsed = _parse(source_job_id, posting)

        published = _timestamp(source_job_id, "first_published", parsed.first_published)
        updated = _timestamp(source_job_id, "updated_at", parsed.updated_at)

        try:
            return NormalizedJob(
                source=SOURCE,
                source_job_id=str(parsed.id),
                company=company,
                title=parsed.title,
                location=parsed.location.name if parsed.location else None,
                # Only the first department is normalized; the full array
                # stays in raw_data (SPEC).
                department=parsed.departments[0].name if parsed.departments else None,
                # Greenhouse sends content HTML-escaped. Unescaping exactly
                # once is the contract: twice would turn `&amp;lt;` into `<`.
                description=(
                    html.unescape(parsed.content)
                    if parsed.content is not None
                    else None
                ),
                job_url=parsed.absolute_url,
                # Applying is a separate authenticated endpoint, not a field.
                apply_url=None,
                source_published_at=published,
                source_updated_at=updated,
                observed_at=observed_at,
                # An independent copy: later changes to the caller's posting
                # must not reach the observation.
                raw_data=copy.deepcopy(posting),
            )
        except ValidationError as exc:
            # The source fields had the right types but violate the domain
            # contract, e.g. a whitespace-only title or URL.
            failure = _invalid(source_job_id, _summarize(exc))
        # Raised outside the except block: a ValidationError's own text quotes
        # input values, so it is neither the cause nor the context.
        raise failure


def _parse(source_job_id: str | None, posting: object) -> GreenhousePosting:
    try:
        return GreenhousePosting.model_validate(posting)
    except ValidationError as exc:
        failure = _invalid(source_job_id, _summarize(exc))
    raise failure


def _check_arguments(*, company: str, observed_at: datetime) -> None:
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        msg = f"observed_at must be timezone-aware, got naive {observed_at!r}"
        raise ValueError(msg)
    if not company.strip():
        msg = "company must be the configured, non-blank display name"
        raise ValueError(msg)


def _known_source_job_id(posting: dict[object, object]) -> str | None:
    """The posting's id as text, if it is one, for error context.

    ``type(...) is int`` rather than isinstance: a JSON ``true`` decodes to
    ``bool``, which is an int subclass and not an id.
    """
    value = posting.get("id")
    return str(value) if type(value) is int else None


def _timestamp(
    source_job_id: str | None, field: str, value: str | None
) -> datetime | None:
    """Parse a source timestamp to UTC. Absent stays absent; never inferred.

    A present value must be ISO 8601 with an explicit offset. A naive value is
    rejected rather than assumed to be UTC (ADR-0003), and an empty string is
    present-but-unparseable, not absent (ADR-0016).
    """
    if value is None:
        return None
    # The value itself is never quoted: it is source content (SPEC, "Safe
    # error messages"). fromisoformat's own error quotes it, so that error is
    # discarded rather than chained.
    moment: datetime | None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        moment = None
    if moment is None:
        raise _invalid(source_job_id, f"{field}: not an ISO 8601 timestamp")
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise _invalid(source_job_id, f"{field}: timestamp has no timezone offset")
    return moment.astimezone(UTC)


def _summarize(exc: ValidationError) -> str:
    """Field locations, validation categories and actual types only.

    Pydantic's ``msg`` is not used: depending on the error type it can quote
    the input or constraint context. The input is read only for its type
    name, e.g. ``id: int_type (got str)``.
    """
    parts = []
    for error in exc.errors(include_url=False, include_context=False):
        location = ".".join(_location_part(part) for part in error["loc"])
        category = _identifier(error["type"], fallback="invalid")
        if category == "missing":
            detail = "missing"
        else:
            actual = _identifier(type(error.get("input")).__name__, fallback="value")
            detail = f"{category} (got {actual})"
        parts.append(f"{location or 'posting'}: {detail}")
    return _bounded("; ".join(parts))


def _location_part(part: int | str) -> str:
    # A location is a schema field name or a list index. A mapping key taken
    # from the input would also appear here, so anything that is not a plain
    # identifier is replaced rather than quoted.
    if isinstance(part, int):
        return str(part)
    return _identifier(part, fallback="<key>")


def _identifier(text: str, *, fallback: str) -> str:
    return text if _IDENTIFIER.fullmatch(text) else fallback


def _invalid(source_job_id: str | None, reason: str) -> NormalizationError:
    return NormalizationError(
        source=SOURCE, source_job_id=source_job_id, reason=_bounded(reason)
    )


def _bounded(text: str) -> str:
    if len(text) <= _REASON_MAX_CHARS:
        return text
    return text[:_REASON_MAX_CHARS] + "..."
