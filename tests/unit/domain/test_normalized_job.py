"""NormalizedJob: the source-independent job contract.

SPEC, "Required normalized fields".
"""

from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest
from pydantic import ValidationError

from job_radar.domain.job import JobSource, NormalizedJob

OBSERVED = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)


def build(**overrides: Any) -> NormalizedJob:  # noqa: ANN401
    fields: dict[str, Any] = {
        "source": JobSource.GREENHOUSE,
        "source_job_id": "8653419002",
        "company": "ExampleCorp",
        "title": "Senior Backend Engineer",
        "job_url": "https://careers.example.com/jobs/8653419002",
        "observed_at": OBSERVED,
        "raw_data": {"id": 8653419002},
    }
    fields.update(overrides)
    return NormalizedJob(**fields)


# --- field contract ---------------------------------------------------------


def test_carries_every_field_named_in_the_spec() -> None:
    expected = {
        "source",
        "source_job_id",
        "company",
        "title",
        "location",
        "department",
        "description",
        "job_url",
        "apply_url",
        "source_published_at",
        "source_updated_at",
        "observed_at",
        "is_active",
        "raw_data",
    }

    assert set(NormalizedJob.model_fields) == expected


def test_optional_fields_default_to_none() -> None:
    job = build()

    assert job.location is None
    assert job.department is None
    assert job.description is None
    assert job.apply_url is None
    assert job.source_published_at is None
    assert job.source_updated_at is None


def test_is_active_defaults_to_true() -> None:
    # ADR-0004: every successfully ingested job is active in the MVP.
    assert build().is_active is True


# --- source_job_id ----------------------------------------------------------


def test_source_job_id_is_a_string() -> None:
    assert build().source_job_id == "8653419002"


def test_source_job_id_rejects_integers() -> None:
    # ADR-0002: ids are text so UUID-based sources need no migration, and
    # observed Greenhouse ids exceed 32-bit range.
    with pytest.raises(ValidationError):
        build(source_job_id=8653419002)


def test_source_job_id_rejects_blank() -> None:
    with pytest.raises(ValidationError):
        build(source_job_id="   ")


# --- timezone handling ------------------------------------------------------


@pytest.mark.parametrize(
    "field",
    ["observed_at", "source_published_at", "source_updated_at"],
)
def test_rejects_naive_datetimes(field: str) -> None:
    # Naive input must fail loudly rather than be assumed to be UTC.
    with pytest.raises(ValidationError):
        build(**{field: datetime(2026, 9, 15, 12, 0)})  # noqa: DTZ001


def test_offset_aware_input_is_converted_to_utc() -> None:
    # Greenhouse emits offsets such as -04:00, never Z (SPEC, observed behavior).
    minus_four = timezone(-timedelta(hours=4))

    job = build(source_updated_at=datetime(2026, 8, 21, 13, 2, 33, tzinfo=minus_four))

    assert job.source_updated_at == datetime(2026, 8, 21, 17, 2, 33, tzinfo=UTC)
    assert job.source_updated_at.utcoffset() == timedelta(0)


def test_source_timestamps_may_be_absent() -> None:
    # ADR-0003: missing source timestamps stay null, never inferred.
    job = build(source_published_at=None, source_updated_at=None)

    assert job.source_published_at is None
    assert job.source_updated_at is None


def test_carries_no_persistence_timestamps() -> None:
    # A normalizer cannot know whether Job Radar has seen this job before, so
    # the observation model must not claim to. Persistence derives both stored
    # columns from observed_at.
    assert "first_seen_at" not in NormalizedJob.model_fields
    assert "last_seen_at" not in NormalizedJob.model_fields


def test_observed_at_is_required() -> None:
    fields = {
        "source": JobSource.GREENHOUSE,
        "source_job_id": "1",
        "company": "ExampleCorp",
        "title": "Engineer",
        "job_url": "https://example.com/1",
        "raw_data": {},
    }
    with pytest.raises(ValidationError):
        NormalizedJob(**fields)


# --- text normalization -----------------------------------------------------


@pytest.mark.parametrize(
    "field", ["location", "department", "description", "apply_url"]
)
def test_blank_optional_text_becomes_none(field: str) -> None:
    # "Unavailable must remain null" - an empty string is absence, not a value.
    assert getattr(build(**{field: "   "}), field) is None


@pytest.mark.parametrize("field", ["company", "title", "job_url"])
def test_required_text_rejects_blank(field: str) -> None:
    with pytest.raises(ValidationError):
        build(**{field: "  "})


def test_required_text_is_stripped() -> None:
    assert build(title="  Senior Backend Engineer  ").title == "Senior Backend Engineer"


# --- raw payload ------------------------------------------------------------


def test_raw_data_is_required() -> None:
    # SPEC requirement 9: the raw payload is preserved for debugging.
    with pytest.raises(ValidationError):
        NormalizedJob(
            source=JobSource.GREENHOUSE,
            source_job_id="1",
            company="ExampleCorp",
            title="Engineer",
            job_url="https://example.com/1",
            observed_at=OBSERVED,
        )  # type: ignore[call-arg]


def test_raw_data_preserves_nested_structure() -> None:
    payload = {"id": 1, "departments": [{"name": "Engineering", "child_ids": []}]}

    assert build(raw_data=payload).raw_data == payload


# --- immutability and strictness --------------------------------------------


def test_job_is_immutable() -> None:
    job = build()

    with pytest.raises(ValidationError):
        job.title = "Changed"  # type: ignore[misc]


def test_unknown_fields_are_rejected() -> None:
    # A source-specific field must never leak into the normalized model.
    with pytest.raises(ValidationError):
        build(requisition_id="R-01182")


def test_source_must_be_a_known_source() -> None:
    with pytest.raises(ValidationError):
        build(source="monster.com")


# --- text PostgreSQL cannot store (SPEC, "Text PostgreSQL cannot store") ----

NUL = "\x00"


def nul_error_fields(error: ValidationError) -> set[str]:
    return {
        ".".join(str(part) for part in e["loc"])
        for e in error.errors()
        if e["type"] == "nul_character"
    }


@pytest.mark.parametrize(
    "field",
    [
        "source_job_id",
        "company",
        "title",
        "job_url",
        "location",
        "department",
        "description",
        "apply_url",
    ],
)
def test_text_containing_nul_is_rejected(field: str) -> None:
    with pytest.raises(ValidationError) as caught:
        build(**{field: f"before{NUL}after"})

    assert nul_error_fields(caught.value) == {field}


@pytest.mark.parametrize(
    "raw_data",
    [
        {"content": f"x{NUL}y"},
        {"departments": [{"name": f"Eng{NUL}"}]},
        {"metadata": [[{"deep": [f"{NUL}"]}]]},
        {f"key{NUL}": "value"},
        {"nested": {f"key{NUL}": 1}},
    ],
    ids=["value", "nested-list", "deeply-nested", "key", "nested-key"],
)
def test_raw_data_containing_nul_anywhere_is_rejected(
    raw_data: dict[str, object],
) -> None:
    with pytest.raises(ValidationError) as caught:
        build(raw_data=raw_data)

    assert nul_error_fields(caught.value) == {"raw_data"}


def test_nul_is_rejected_not_stripped() -> None:
    # Stripping would store a value the source never sent.
    with pytest.raises(ValidationError):
        build(title=f"{NUL}")
    with pytest.raises(ValidationError):
        build(location=f"  {NUL}  ")


def test_text_without_nul_is_kept_exactly() -> None:
    payload = {"content": "a\u0001b\tc\n", "list": ["é", 0, None, True]}

    job = build(description="a\u0001b", raw_data=payload)

    assert job.description == "a\u0001b"
    assert job.raw_data == payload


#: Range ends and the first low surrogate. Any surrogate left in a `str` is
#: unpaired: a valid pair is one non-BMP code point.
SURROGATES = ["\ud800", "\udbff", "\udc00", "\udfff"]


def surrogate_error_fields(error: ValidationError) -> set[str]:
    return {
        ".".join(str(part) for part in e["loc"])
        for e in error.errors()
        if e["type"] == "surrogate_character"
    }


@pytest.mark.parametrize("surrogate", SURROGATES, ids=lambda s: f"U+{ord(s):04X}")
@pytest.mark.parametrize(
    "field",
    [
        "source_job_id",
        "company",
        "title",
        "job_url",
        "location",
        "department",
        "description",
        "apply_url",
    ],
)
def test_text_containing_a_surrogate_is_rejected(field: str, surrogate: str) -> None:
    with pytest.raises(ValidationError) as caught:
        build(**{field: f"before{surrogate}after"})

    assert surrogate_error_fields(caught.value) == {field}


def test_a_reversed_surrogate_pair_is_rejected() -> None:
    # Low then high is not a pair: two unpaired code points.
    with pytest.raises(ValidationError) as caught:
        build(title="\ude00\ud83d")

    assert surrogate_error_fields(caught.value) == {"title"}


@pytest.mark.parametrize(
    "raw_data",
    [
        {"content": "x\ud800y"},
        {"departments": [{"name": "Eng\udc00"}]},
        {"metadata": [[{"deep": ["\udfff"]}]]},
        {"key\ud800": "value"},
        {"nested": {"key\udc00": 1}},
    ],
    ids=["value", "nested-list", "deeply-nested", "key", "nested-key"],
)
def test_raw_data_containing_a_surrogate_anywhere_is_rejected(
    raw_data: dict[str, object],
) -> None:
    with pytest.raises(ValidationError) as caught:
        build(raw_data=raw_data)

    assert surrogate_error_fields(caught.value) == {"raw_data"}


def test_a_surrogate_is_rejected_not_stripped() -> None:
    with pytest.raises(ValidationError):
        build(title="\ud800")
    with pytest.raises(ValidationError):
        build(location="  \udc00  ")


def test_nul_takes_precedence_over_a_surrogate() -> None:
    with pytest.raises(ValidationError) as in_text:
        build(title=f"\ud800{NUL}")
    assert nul_error_fields(in_text.value) == {"title"}
    assert surrogate_error_fields(in_text.value) == set()

    # In a payload, regardless of which one the traversal reaches first.
    for raw_data in (
        {"a": "\ud800", "b": f"{NUL}"},
        {"a": f"{NUL}", "b": "\ud800"},
        {"a": [{"deep": "\udc00"}], "b": [f"{NUL}"]},
    ):
        with pytest.raises(ValidationError) as in_payload:
            build(raw_data=raw_data)
        assert nul_error_fields(in_payload.value) == {"raw_data"}
        assert surrogate_error_fields(in_payload.value) == set()


def test_valid_unicode_outside_the_surrogate_range_is_kept_exactly() -> None:
    # Non-BMP characters, the code points either side of the surrogate block,
    # the highest code point and the replacement character.
    text = "\U0001f600 \U00020000 ퟿  \U0010ffff � café"
    payload = {"content": text, "emoji\U0001f680": ["\U0001f1f2\U0001f1fd"]}

    job = build(title=text, description=text, raw_data=payload)

    assert job.title == text
    assert job.description == text
    assert job.raw_data == payload


def test_a_very_deeply_nested_payload_does_not_exhaust_the_stack() -> None:
    payload: dict[str, object] = {"leaf": "ok"}
    for _ in range(5_000):
        payload = {"n": payload}

    assert build(raw_data=payload).raw_data is not None


def test_a_surrogate_at_the_bottom_of_a_very_deep_payload_is_rejected() -> None:
    payload: dict[str, object] = {"leaf": "\ud800"}
    for _ in range(5_000):
        payload = {"n": payload}

    with pytest.raises(ValidationError) as caught:
        build(raw_data=payload)

    assert surrogate_error_fields(caught.value) == {"raw_data"}
