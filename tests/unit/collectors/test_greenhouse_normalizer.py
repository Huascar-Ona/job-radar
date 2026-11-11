"""GreenhouseNormalizer: one Greenhouse posting to one NormalizedJob.

The mapping under test is SPEC's "Greenhouse field mapping"; the error
taxonomy is ADR-0016's. Everything here is pure translation against the
captured fixtures in tests/fixtures/greenhouse/: no database, no network.
"""

import copy
import json
import traceback
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from job_radar.collectors.base import Normalizer
from job_radar.collectors.greenhouse.normalizer import GreenhouseNormalizer
from job_radar.domain.errors import NormalizationError
from job_radar.domain.job import JobSource, NormalizedJob

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "greenhouse"

OBSERVED = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)

#: Deliberately different from the payload's `company_name` ("ExampleCorp"),
#: so a test can tell which one was used.
COMPANY = "Example Corp (configured)"


def load_postings(name: str) -> list[dict[str, object]]:
    body = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return list(body["jobs"])


CONTENT = load_postings("board_jobs_content.json")
DEGRADED = load_postings("board_jobs_degraded.json")


def posting_with(**fields: object) -> dict[str, object]:
    """The first content fixture posting, with fields replaced or added."""
    posting = copy.deepcopy(CONTENT[0])
    posting.update(fields)
    return posting


def posting_without(*keys: str) -> dict[str, object]:
    posting = copy.deepcopy(CONTENT[0])
    for key in keys:
        del posting[key]
    return posting


def normalize(
    posting: object, *, company: str = COMPANY, observed_at: datetime = OBSERVED
) -> NormalizedJob:
    return GreenhouseNormalizer().normalize(
        posting, company=company, observed_at=observed_at
    )


def rejection(posting: object) -> NormalizationError:
    with pytest.raises(NormalizationError) as info:
        normalize(posting)
    return info.value


# --- interface ---------------------------------------------------------------


def test_satisfies_the_normalizer_protocol() -> None:
    # The annotated assignment is the real check: mypy verifies the full
    # `normalize` signature against the Protocol. isinstance on a
    # runtime_checkable Protocol only confirms the method name exists.
    normalizer: Normalizer = GreenhouseNormalizer()

    assert isinstance(normalizer, Normalizer)


# --- valid postings ----------------------------------------------------------


@pytest.mark.parametrize("posting", CONTENT, ids=lambda p: str(p["id"]))
def test_normalizes_every_content_fixture_posting(posting: dict[str, object]) -> None:
    job = normalize(posting)

    assert job.source_job_id == str(posting["id"])


def test_maps_every_field_per_spec() -> None:
    job = normalize(CONTENT[0])

    assert job.source is JobSource.GREENHOUSE
    assert job.source_job_id == "8653419002"
    assert job.company == COMPANY
    assert job.title == "Senior Backend Engineer"
    assert job.location == "Remote - Illinois"
    assert job.department == "Engineering"
    assert job.description == (
        "<p>We are hiring a backend engineer.</p>\n<p>You\u2019ll build things.</p>"
    )
    assert job.job_url == (
        "https://careers.examplecorp.com/jobs/8653419002?gh_jid=8653419002"
    )
    assert job.apply_url is None
    assert job.source_published_at == datetime(2026, 7, 28, 13, 47, 45, tzinfo=UTC)
    assert job.source_updated_at == datetime(2026, 8, 21, 17, 2, 33, tzinfo=UTC)
    assert job.observed_at == OBSERVED
    assert job.is_active is True
    assert job.raw_data == CONTENT[0]


def test_second_fixture_posting_converts_its_own_offset() -> None:
    job = normalize(CONTENT[1])

    assert job.source_published_at == datetime(2026, 9, 1, 13, 0, tzinfo=UTC)
    assert job.source_updated_at == datetime(2026, 9, 2, 16, 15, tzinfo=UTC)
    assert job.location == "Mexico City, Mexico"
    assert job.department == "Platform"


def test_id_beyond_32_bits_is_converted_to_text() -> None:
    big = 2**40 + 7

    assert normalize(posting_with(id=big)).source_job_id == str(big)


def test_company_comes_from_configuration_not_company_name() -> None:
    assert CONTENT[0]["company_name"] == "ExampleCorp"

    assert normalize(CONTENT[0], company="Configured Name").company == (
        "Configured Name"
    )


def test_company_name_is_not_required() -> None:
    assert normalize(posting_without("company_name")).company == COMPANY


@pytest.mark.parametrize("field", ["first_published", "updated_at"])
def test_source_offsets_become_utc_preserving_the_instant(field: str) -> None:
    raw = "2026-08-21T13:02:33-04:00"
    job = normalize(posting_with(**{field: raw}))

    converted = (
        job.source_published_at if field == "first_published" else job.source_updated_at
    )
    assert converted == datetime.fromisoformat(raw)
    assert converted is not None
    assert converted.utcoffset() == timedelta(0)


def test_z_suffix_is_parsed_as_utc() -> None:
    job = normalize(DEGRADED[0])

    assert job.source_updated_at == datetime(2026, 9, 10, tzinfo=UTC)


def test_observed_at_is_the_injected_instant_in_utc() -> None:
    mexico_city = timezone(timedelta(hours=-6))
    injected = datetime(2026, 9, 15, 6, 0, tzinfo=mexico_city)

    job = normalize(CONTENT[0], observed_at=injected)

    assert job.observed_at == injected
    assert job.observed_at.utcoffset() == timedelta(0)


def test_missing_publication_time_is_never_backfilled() -> None:
    job = normalize(posting_without("first_published"))

    assert job.source_published_at is None
    assert job.source_updated_at is not None
    assert job.observed_at == OBSERVED


def test_raw_data_is_the_complete_posting() -> None:
    job = normalize(CONTENT[0])

    assert job.raw_data == CONTENT[0]
    # Fields the normalizer does not read are kept, not dropped.
    assert job.raw_data["internal_job_id"] == CONTENT[0]["internal_job_id"]
    assert job.raw_data["offices"] == CONTENT[0]["offices"]


def test_raw_data_is_independent_of_the_input() -> None:
    posting = posting_with()
    snapshot = copy.deepcopy(posting)
    job = normalize(posting)

    posting["title"] = "mutated"
    location = posting["location"]
    assert isinstance(location, dict)
    location["name"] = "mutated"

    assert job.raw_data == snapshot


def test_normalizing_does_not_modify_the_input() -> None:
    posting = posting_with()
    snapshot = copy.deepcopy(posting)

    normalize(posting)

    assert posting == snapshot


def test_same_input_gives_the_same_observation() -> None:
    assert normalize(CONTENT[0]) == normalize(CONTENT[0])


# --- ids ---------------------------------------------------------------------


def test_missing_id_is_rejected() -> None:
    error = rejection(posting_without("id"))

    assert error.source == "greenhouse"
    assert error.source_job_id is None
    assert "id" in error.reason


@pytest.mark.parametrize(
    "bad_id",
    [None, True, False, "8653419002", 8653419002.0, [], {}],
    ids=repr,
)
def test_id_that_is_not_a_json_integer_is_rejected(bad_id: object) -> None:
    error = rejection(posting_with(id=bad_id))

    assert error.source_job_id is None
    assert "id" in error.reason


@pytest.mark.parametrize("value", [0, -1])
def test_non_positive_integer_id_is_accepted(value: int) -> None:
    # No positive-id rule exists, so none is invented.
    assert normalize(posting_with(id=value)).source_job_id == str(value)


# --- required text fields ----------------------------------------------------


@pytest.mark.parametrize("field", ["title", "absolute_url"])
def test_missing_required_field_is_rejected(field: str) -> None:
    error = rejection(posting_without(field))

    assert error.source_job_id == "8653419002"
    assert field in error.reason


@pytest.mark.parametrize("field", ["title", "absolute_url"])
@pytest.mark.parametrize("value", [None, 42, ["text"]], ids=repr)
def test_required_field_of_the_wrong_type_is_rejected(
    field: str, value: object
) -> None:
    error = rejection(posting_with(**{field: value}))

    assert error.source_job_id == "8653419002"
    assert field in error.reason


@pytest.mark.parametrize("value", ["", "   ", "\n\t"], ids=repr)
def test_blank_title_is_rejected(value: str) -> None:
    error = rejection(posting_with(title=value))

    assert error.source_job_id == "8653419002"
    assert "title" in error.reason


@pytest.mark.parametrize("value", ["", "   "], ids=repr)
def test_blank_absolute_url_is_rejected(value: str) -> None:
    error = rejection(posting_with(absolute_url=value))

    assert error.source_job_id == "8653419002"
    assert "job_url" in error.reason


def test_absolute_url_is_not_host_restricted() -> None:
    url = "https://jobs.some-customer-domain.example/apply?ref=1"

    assert normalize(posting_with(absolute_url=url)).job_url == url


# --- timestamps --------------------------------------------------------------


@pytest.mark.parametrize("field", ["first_published", "updated_at"])
@pytest.mark.parametrize(
    "value",
    [
        "yesterday",
        "",
        "2026-13-45T00:00:00Z",
        "2026-09-10T00:00:00",
        "2026-09-10",
        1700000000,
        True,
    ],
    ids=repr,
)
def test_invalid_timestamp_is_rejected(field: str, value: object) -> None:
    error = rejection(posting_with(**{field: value}))

    assert error.source_job_id == "8653419002"
    assert field in error.reason


def test_naive_timestamp_reason_names_the_missing_offset() -> None:
    error = rejection(posting_with(updated_at="2026-09-10T00:00:00"))

    assert "offset" in error.reason


# --- optional fields: absent, null, empty ------------------------------------


def test_degraded_posting_with_absent_and_empty_fields() -> None:
    job = normalize(DEGRADED[0])

    assert job.source_job_id == "9000000001"
    assert job.location is None  # {"name": null}
    assert job.department is None  # []
    assert job.description is None  # content absent
    assert job.source_published_at is None  # first_published absent
    assert job.raw_data == DEGRADED[0]


def test_degraded_posting_with_explicit_nulls() -> None:
    job = normalize(DEGRADED[1])

    assert job.source_job_id == "9000000002"
    assert job.location is None
    assert job.department is None
    assert job.description is None
    assert job.source_published_at is None
    assert job.source_updated_at is None


def test_degraded_posting_without_id_is_rejected() -> None:
    error = rejection(DEGRADED[2])

    assert error.source_job_id is None


def test_one_invalid_posting_does_not_affect_the_others() -> None:
    jobs: list[NormalizedJob] = []
    errors: list[NormalizationError] = []
    for posting in DEGRADED:
        try:
            jobs.append(normalize(posting))
        except NormalizationError as exc:
            errors.append(exc)

    assert [job.source_job_id for job in jobs] == ["9000000001", "9000000002"]
    assert len(errors) == 1


@pytest.mark.parametrize(
    "field", ["location", "departments", "content", "first_published", "updated_at"]
)
def test_absent_optional_field_is_not_an_error(field: str) -> None:
    normalize(posting_without(field))


@pytest.mark.parametrize(
    ("field", "value", "attribute"),
    [
        ("location", None, "location"),
        ("location", {"name": "   "}, "location"),
        ("location", {}, "location"),
        ("departments", None, "department"),
        ("departments", [], "department"),
        ("departments", [{"id": 1}], "department"),
        ("content", None, "description"),
        ("content", "", "description"),
    ],
    ids=repr,
)
def test_null_or_empty_optional_field_normalizes_to_null(
    field: str, value: object, attribute: str
) -> None:
    job = normalize(posting_with(**{field: value}))

    assert getattr(job, attribute) is None


def test_first_department_is_taken_when_several() -> None:
    departments = [{"name": "Engineering"}, {"name": "Platform"}]

    job = normalize(posting_with(departments=departments))

    assert job.department == "Engineering"
    assert job.raw_data["departments"] == departments


def test_location_is_carried_across_verbatim() -> None:
    compound = "San Francisco, CA | Seattle, WA"

    assert normalize(posting_with(location={"name": compound})).location == compound


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("location", "Remote"),
        ("location", ["Remote"]),
        ("location", {"name": 42}),
        ("departments", {"name": "Engineering"}),
        ("departments", [None]),
        ("departments", [{"name": 7}]),
        ("departments", ({"name": "Engineering"},)),
        ("content", 42),
        ("content", ["text"]),
    ],
    ids=repr,
)
def test_known_optional_field_of_the_wrong_type_is_rejected(
    field: str, value: object
) -> None:
    error = rejection(posting_with(**{field: value}))

    assert error.source_job_id == "8653419002"
    assert field in error.reason


# --- HTML entities -----------------------------------------------------------


def test_content_is_unescaped_exactly_once() -> None:
    job = normalize(posting_with(content="&amp;lt;p&amp;gt; R&amp;amp;D"))

    assert job.description == "&lt;p&gt; R&amp;D"


def test_unescaped_content_keeps_its_html_tags() -> None:
    job = normalize(posting_with(content="&lt;p&gt;Hi&lt;/p&gt;"))

    assert job.description == "<p>Hi</p>"


def test_title_is_not_entity_decoded() -> None:
    assert normalize(posting_with(title="R&amp;D Engineer")).title == "R&amp;D Engineer"


# --- unknown fields and malformed postings -----------------------------------


def test_unknown_fields_are_ignored_and_kept_in_raw_data() -> None:
    posting = posting_with(
        brand_new_field={"nested": [1, 2]},
        location={"name": "Remote", "geo": {"lat": 19.4}},
    )

    job = normalize(posting)

    assert job.location == "Remote"
    assert job.raw_data["brand_new_field"] == {"nested": [1, 2]}
    assert job.raw_data["location"] == {"name": "Remote", "geo": {"lat": 19.4}}


@pytest.mark.parametrize(
    "posting", ["not-an-object", [], None, 42, ["id", 8653419002]], ids=repr
)
def test_non_object_posting_is_rejected(posting: object) -> None:
    error = rejection(posting)

    assert error.source_job_id is None
    assert "not an object" in error.reason


def test_empty_object_is_rejected_without_an_id() -> None:
    error = rejection({})

    assert error.source_job_id is None


def test_rejection_reason_does_not_embed_the_payload() -> None:
    marker = "CONTENT-MARKER-"
    error = rejection(posting_with(id=None, content=marker * 2_000))

    assert marker not in str(error)
    assert len(str(error)) < 500


def test_rejected_timestamp_value_is_not_quoted() -> None:
    error = rejection(posting_with(updated_at="9" * 5_000))

    assert error.reason == "updated_at: not an ISO 8601 timestamp"


# --- safe rejection reasons (SPEC, "Safe error messages") -------------------

SECRET = "SECRET-MARKER-9e2d"


def assert_safe(error: NormalizationError) -> None:
    """No marker anywhere a caller could read it, and no exception chain."""
    assert error.__cause__ is None
    assert error.__context__ is None
    for text in (
        error.reason,
        str(error),
        repr(error),
        repr(error.args),
        repr(error.log_context()),
        "".join(traceback.format_exception(error)),
    ):
        assert SECRET not in text, text


@pytest.mark.parametrize("field", ["first_published", "updated_at"])
@pytest.mark.parametrize(
    ("value", "reason"),
    [
        (SECRET, "not an ISO 8601 timestamp"),
        (f"2026-09-10T00:00:00{SECRET}", "not an ISO 8601 timestamp"),
        ("2026-09-10T00:00:00", "timestamp has no timezone offset"),
    ],
    ids=["text", "suffix", "naive"],
)
def test_a_rejected_timestamp_reason_names_the_field_not_the_value(
    field: str, value: str, reason: str
) -> None:
    error = rejection(posting_with(**{field: value}))

    assert error.reason == f"{field}: {reason}"
    assert_safe(error)


def test_a_type_mismatch_reason_gives_category_and_actual_type() -> None:
    error = rejection(posting_with(id=SECRET))

    assert error.reason == "id: int_type (got str)"
    assert_safe(error)


def test_a_missing_field_reason_says_missing() -> None:
    error = rejection(posting_without("title"))

    assert error.reason == "title: missing"


def test_a_nested_mismatch_reason_keeps_the_location() -> None:
    error = rejection(posting_with(departments=[{"name": 7}]))

    assert error.reason == "departments.0.name: string_type (got int)"


@pytest.mark.parametrize(
    "fields",
    [
        {"title": [SECRET]},
        {"absolute_url": {"href": SECRET}},
        {"location": SECRET},
        {"content": [SECRET]},
        {"departments": [{"name": [SECRET]}]},
    ],
    ids=["title", "absolute_url", "location", "content", "nested"],
)
def test_validation_reasons_never_quote_source_values(
    fields: dict[str, object],
) -> None:
    error = rejection(posting_with(**fields))

    assert_safe(error)


def test_a_domain_contract_violation_is_not_chained() -> None:
    error = rejection(posting_with(title=f"  {' ' * 10}"))

    assert error.reason == "title: string_too_short (got str)"
    assert_safe(error)


def test_reason_names_every_failing_field() -> None:
    error = rejection(posting_with(title=None, absolute_url=None))

    assert "title" in error.reason
    assert "absolute_url" in error.reason


# --- NUL characters (SPEC, "Text PostgreSQL cannot store") -----------------

NUL = "\x00"


@pytest.mark.parametrize(
    ("fields", "reason"),
    [
        (
            {"title": f"Engineer{NUL}"},
            "title: nul_character (got str); raw_data: nul_character (got dict)",
        ),
        (
            {"content": f"&lt;p&gt;{NUL}&lt;/p&gt;"},
            "description: nul_character (got str); raw_data: nul_character (got dict)",
        ),
        (
            {"location": {"name": f"Remote{NUL}"}},
            "location: nul_character (got str); raw_data: nul_character (got dict)",
        ),
        (
            {"departments": [{"name": f"Eng{NUL}"}]},
            "department: nul_character (got str); raw_data: nul_character (got dict)",
        ),
        ({"metadata": [{"value": f"x{NUL}"}]}, "raw_data: nul_character (got dict)"),
        (
            {"offices": [{"name": "HQ", f"k{NUL}": 1}]},
            "raw_data: nul_character (got dict)",
        ),
    ],
    ids=["title", "content", "location", "department", "unmapped", "nested-key"],
)
def test_a_posting_containing_nul_is_invalid(
    fields: dict[str, object], reason: str
) -> None:
    error = rejection(posting_with(**fields))

    assert error.source_job_id == "8653419002"
    assert error.reason == reason
    assert error.__cause__ is None
    assert error.__context__ is None


def test_a_nul_rejection_does_not_alter_the_posting() -> None:
    posting = posting_with(content=f"a{NUL}b")
    snapshot = copy.deepcopy(posting)

    rejection(posting)

    assert posting == snapshot


def test_a_nul_in_one_posting_does_not_affect_the_next() -> None:
    rejection(posting_with(title=f"x{NUL}"))

    assert normalize(posting_with(title="Engineer")).title == "Engineer"


# --- unpaired surrogates (SPEC, "Text PostgreSQL cannot store") --------------


def decoded(json_fields: str) -> dict[str, object]:
    """The fixture posting with fields decoded from JSON text, as the collector
    receives them: a lone `\\ud800` escape decodes to an unpaired surrogate."""
    fields = json.loads(json_fields)
    assert isinstance(fields, dict)
    return posting_with(**fields)


@pytest.mark.parametrize(
    ("json_fields", "reason"),
    [
        (
            r'{"title": "Engineer\ud800"}',
            "title: surrogate_character (got str); "
            "raw_data: surrogate_character (got dict)",
        ),
        (
            r'{"title": "Engineer\udc00"}',
            "title: surrogate_character (got str); "
            "raw_data: surrogate_character (got dict)",
        ),
        (
            r'{"content": "&lt;p&gt;\ud800&lt;/p&gt;"}',
            "description: surrogate_character (got str); "
            "raw_data: surrogate_character (got dict)",
        ),
        (
            r'{"location": {"name": "Remote\udfff"}}',
            "location: surrogate_character (got str); "
            "raw_data: surrogate_character (got dict)",
        ),
        (
            r'{"departments": [{"name": "Eng\udbff"}]}',
            "department: surrogate_character (got str); "
            "raw_data: surrogate_character (got dict)",
        ),
        (
            r'{"metadata": [{"value": "x\ud800"}]}',
            "raw_data: surrogate_character (got dict)",
        ),
        (
            r'{"offices": [{"name": "HQ", "k\udc00": 1}]}',
            "raw_data: surrogate_character (got dict)",
        ),
        (
            r'{"title": "\ude00\ud83d"}',
            "title: surrogate_character (got str); "
            "raw_data: surrogate_character (got dict)",
        ),
    ],
    ids=[
        "title-high",
        "title-low",
        "content",
        "location",
        "department",
        "unmapped",
        "nested-key",
        "reversed-pair",
    ],
)
def test_a_posting_containing_an_unpaired_surrogate_is_invalid(
    json_fields: str, reason: str
) -> None:
    error = rejection(decoded(json_fields))

    assert error.source_job_id == "8653419002"
    assert error.reason == reason
    assert error.__cause__ is None
    assert error.__context__ is None


def test_a_surrogate_rejection_quotes_no_source_text() -> None:
    error = rejection(decoded(r'{"title": "secret-\ud800-title"}'))

    assert "secret" not in str(error)
    # The message itself is loggable and storable as UTF-8.
    str(error).encode("utf-8")
    error.reason.encode("utf-8")


def test_a_surrogate_rejection_does_not_alter_the_posting() -> None:
    posting = decoded(r'{"content": "a\ud800b"}')
    snapshot = copy.deepcopy(posting)

    rejection(posting)

    assert posting == snapshot


def test_a_surrogate_in_one_posting_does_not_affect_the_next() -> None:
    rejection(decoded(r'{"title": "x\ud800"}'))

    assert normalize(posting_with(title="Engineer")).title == "Engineer"


def test_a_valid_escaped_surrogate_pair_is_kept() -> None:
    # The pair decodes to one non-BMP code point, U+1F680.
    job = normalize(
        decoded(
            r'{"title": "Engineer 🚀", '
            r'"content": "&lt;p&gt;🚀&lt;/p&gt;", '
            r'"metadata": [{"value": "🚀"}]}'
        )
    )

    assert job.title == "Engineer \U0001f680"
    assert job.description == "<p>\U0001f680</p>"
    assert job.raw_data["metadata"] == [{"value": "\U0001f680"}]


def test_nul_takes_precedence_over_a_surrogate_in_a_posting() -> None:
    error = rejection(decoded(r'{"title": "a\u0000b", "metadata": ["\ud800"]}'))

    assert error.reason == (
        "title: nul_character (got str); raw_data: nul_character (got dict)"
    )


# --- caller errors are not invalid postings ----------------------------------


def test_naive_observed_at_is_a_caller_error() -> None:
    naive = datetime(2026, 9, 15, 12, 0)  # noqa: DTZ001 - the case under test

    with pytest.raises(ValueError, match="observed_at"):
        normalize(CONTENT[0], observed_at=naive)


@pytest.mark.parametrize("company", ["", "   "], ids=repr)
def test_blank_company_is_a_caller_error(company: str) -> None:
    with pytest.raises(ValueError, match="company"):
        normalize(CONTENT[0], company=company)


def test_caller_errors_are_raised_before_the_posting_is_inspected() -> None:
    # A bad argument must surface as a bug, never be counted as `invalid`.
    with pytest.raises(ValueError, match="company"):
        normalize("not-an-object", company="")


def test_caller_errors_are_not_normalization_errors() -> None:
    assert not issubclass(NormalizationError, ValueError)
