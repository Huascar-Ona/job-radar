"""The normalized job observation: the contract every source is translated into.

This model is the architectural boundary. Collectors and normalizers translate
source payloads into it; nothing downstream may look past it at source-specific
structures (ARCHITECTURE, "Architectural Boundary").

A NormalizedJob is one *observation* of a source job. It deliberately does not
carry ``first_seen_at`` or ``last_seen_at``: a normalizer cannot know whether
Job Radar has seen this job before. It carries ``observed_at``, and persistence
derives both stored columns from it.
"""

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


# Timezone-aware only. AwareDatetime rejects naive input rather than assuming
# UTC; the AfterValidator then normalizes the offset away (ADR-0003).
UtcDatetime = Annotated[AwareDatetime, AfterValidator(_to_utc)]

RequiredText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
# _blank_to_none already strips, so no StringConstraints here: they cannot be
# applied to an optional type.
OptionalText = Annotated[str | None, BeforeValidator(_blank_to_none)]


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

    raw_data: Mapping[str, object] = Field(
        description="The source payload as received (SPEC requirement 9)."
    )

    @property
    def identity(self) -> tuple[JobSource, str]:
        """The uniqueness key: (source, source_job_id) (ADR-0002)."""
        return (self.source, self.source_job_id)
