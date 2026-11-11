"""The normalized job observation: the contract every source is translated into.

This model is the architectural boundary. Collectors and normalizers translate
source payloads into it; nothing downstream may look past it at source-specific
structures (ARCHITECTURE, "Architectural Boundary").

A NormalizedJob is one *observation* of a source job. It deliberately does not
carry ``first_seen_at`` or ``last_seen_at``: a normalizer cannot know whether
Job Radar has seen this job before. It carries ``observed_at``, and persistence
derives both stored columns from it.
"""

import re
from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
)
from pydantic_core import PydanticCustomError


class JobSource(StrEnum):
    """Supported job sources.

    Adding a member here is the only place the set of sources is declared.
    """

    GREENHOUSE = "greenhouse"


def _to_utc(moment: datetime) -> datetime:
    """Convert an aware datetime to UTC, preserving the instant."""
    return moment.astimezone(UTC)


def _blank_to_none(value: object) -> object:
    """Treat whitespace-only text as absence.

    An empty string is not a value the source provided; SPEC requires
    unavailable fields to be null rather than inferred.
    """
    if isinstance(value, str):
        return value.strip() or None
    return value


#: Text PostgreSQL cannot store (SPEC, "Text PostgreSQL cannot store"). A
#: value carrying either would fail at persistence, so it is rejected here,
#: where it makes one posting invalid instead. The value is rejected, never
#: stripped or replaced: altering source data would be inventing it.
#:
#: U+0000 cannot be stored: not in `text`, and not as `\u0000` in `jsonb`.
_NUL = "\x00"

#: A surrogate code point (U+D800 to U+DFFF) is not a character and has no
#: UTF-8 encoding. A JSON `\ud800` escape without its pair decodes to one, and
#: psycopg then raises UnicodeEncodeError, which is not a database error, so
#: the ingestion service would treat it as a programming error and let it
#: propagate rather than fail one company. A valid escaped pair decodes to a
#: single non-BMP code point, so any surrogate left in a `str` is unpaired.
_SURROGATE = re.compile("[\ud800-\udfff]")


def _nul_error() -> PydanticCustomError:
    return PydanticCustomError("nul_character", "text contains a NUL character")


def _surrogate_error() -> PydanticCustomError:
    return PydanticCustomError(
        "surrogate_character", "text contains an unpaired surrogate code point"
    )


def _reject_unstorable(value: str) -> str:
    # NUL is checked first, so a value with both reports `nul_character`.
    if _NUL in value:
        raise _nul_error()
    if _SURROGATE.search(value):
        raise _surrogate_error()
    return value


def _reject_unstorable_input(value: object) -> object:
    """Check text before pydantic's own string handling sees it.

    ``StringConstraints`` cannot process a surrogate and would report its own
    ``string_unicode`` error instead of ``surrogate_character``. Stripping
    never removes a NUL, so checking before it changes nothing for NUL.
    Anything that is not text is left for the type check to reject.
    """
    return _reject_unstorable(value) if isinstance(value, str) else value


def _reject_optional_unstorable(value: str | None) -> str | None:
    return None if value is None else _reject_unstorable(value)


def _reject_unstorable_in_payload(
    payload: Mapping[str, object],
) -> Mapping[str, object]:
    """Reject unstorable text in any key or string value, however deeply nested.

    A NUL anywhere takes precedence over a surrogate anywhere, so the category
    does not depend on traversal order. Iterative rather than recursive, so a
    deeply nested payload cannot exhaust the interpreter stack here.
    """
    surrogate_seen = False
    pending: list[object] = [payload]
    while pending:
        value = pending.pop()
        if isinstance(value, str):
            if _NUL in value:
                raise _nul_error()
            if not surrogate_seen and _SURROGATE.search(value):
                surrogate_seen = True
        elif isinstance(value, Mapping):
            pending.extend(value.keys())
            pending.extend(value.values())
        elif isinstance(value, list | tuple):
            pending.extend(value)
    if surrogate_seen:
        raise _surrogate_error()
    return payload


# Timezone-aware only. AwareDatetime rejects naive input rather than assuming
# UTC; the AfterValidator then normalizes the offset away (ADR-0003).
UtcDatetime = Annotated[AwareDatetime, AfterValidator(_to_utc)]

# Order matters: metadata applies left to right, so the BeforeValidator wraps
# the constrained string and runs first. Listed before the constraints, it
# would silently drop them.
RequiredText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
    BeforeValidator(_reject_unstorable_input),
]
# _blank_to_none already strips, so no StringConstraints here: they cannot be
# applied to an optional type.
OptionalText = Annotated[
    str | None,
    BeforeValidator(_blank_to_none),
    AfterValidator(_reject_optional_unstorable),
]
SourcePayload = Annotated[
    Mapping[str, object], AfterValidator(_reject_unstorable_in_payload)
]


class NormalizedJob(BaseModel):
    """A job as Job Radar represents it, independent of where it came from."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source: JobSource
    source_job_id: RequiredText = Field(
        description="Source-assigned id, always text (ADR-0002)."
    )
    company: RequiredText = Field(
        description="Configured display name of the board (ADR-0007)."
    )
    title: RequiredText
    location: OptionalText = None
    department: OptionalText = None
    description: OptionalText = None
    job_url: RequiredText
    apply_url: OptionalText = None

    source_published_at: UtcDatetime | None = Field(
        default=None,
        description="Reported by the source. Null when unavailable; never inferred.",
    )
    source_updated_at: UtcDatetime | None = Field(
        default=None,
        description="Reported by the source. Null when unavailable; never inferred.",
    )

    observed_at: UtcDatetime = Field(
        description=(
            "Instant at which this observation was made, from the injected "
            "clock. On insert it becomes both first_seen_at and last_seen_at; "
            "on conflict it advances last_seen_at only. It is not itself a "
            "column on the jobs table."
        )
    )

    is_active: bool = Field(
        default=True, description="Always true in the MVP (ADR-0004)."
    )

    raw_data: SourcePayload = Field(
        description="The source payload as received (SPEC requirement 9)."
    )

    @property
    def identity(self) -> tuple[JobSource, str]:
        """The uniqueness key: (source, source_job_id) (ADR-0002)."""
        return (self.source, self.source_job_id)
