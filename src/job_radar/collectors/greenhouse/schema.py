"""The Greenhouse posting fields the normalizer reads, and their types.

Private to the Greenhouse package: nothing outside it may import this module
(ARCHITECTURE, "Architectural Boundary"; enforced by
tests/unit/domain/test_domain_boundary.py).

Strict mode is deliberate. Pydantic's default coercion would turn ``"123"``
or ``123.0`` into an id and a bare number into a datetime: values the source
never sent. A known field with the wrong type is rejected instead (ADR-0016).

Unknown fields are ignored, not forbidden: providers add fields, and the
complete posting is preserved separately as ``raw_data``. Tolerant at the
edge, strict in the domain.

Timestamps are declared as text and parsed by the normalizer, which owns the
rule that a timestamp without an explicit offset is invalid.
"""

from pydantic import BaseModel, ConfigDict


class _SourceModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore", frozen=True)


class GreenhouseLocation(_SourceModel):
    name: str | None = None


class GreenhouseDepartment(_SourceModel):
    name: str | None = None


class GreenhousePosting(_SourceModel):
    """One element of the Board API's ``jobs`` array, as far as it is used.

    Required: ``id`` (a JSON integer), ``title`` and ``absolute_url``.
    Everything else may be absent or null without making the posting invalid.
    """

    id: int
    title: str
    absolute_url: str
    location: GreenhouseLocation | None = None
    departments: list[GreenhouseDepartment] | None = None
    content: str | None = None
    first_published: str | None = None
    updated_at: str | None = None
