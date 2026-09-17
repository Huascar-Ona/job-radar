"""The database itself must enforce the persistence invariants.

Every constraint here is exercised with raw SQL so that a passing test proves
PostgreSQL rejects the write, not that SQLAlchemy declined to send it
(ADR-0010).
"""

from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest
from sqlalchemy import Connection, Engine, inspect, text
from sqlalchemy.exc import DBAPIError, IntegrityError

pytestmark = pytest.mark.integration

EXPECTED_TABLES = {"companies", "jobs", "crawl_runs", "crawl_company_results"}
DEFAULT_BOARD_TOKEN = "examplecorp"
OBSERVED = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)


# --- helpers ----------------------------------------------------------------


def insert_company(
    connection: Connection,
    *,
    source: str = "greenhouse",
    board_token: str = DEFAULT_BOARD_TOKEN,
) -> int:
    return int(
        connection.execute(
            text(
                "INSERT INTO companies (source, board_token, display_name) "
                "VALUES (:source, :token, :name) RETURNING id"
            ),
            {"source": source, "token": board_token, "name": "ExampleCorp"},
        ).scalar_one()
    )


def insert_job(connection: Connection, company_id: int, **overrides: Any) -> int:  # noqa: ANN401
    """Insert a job, letting any column be overridden by keyword."""
    params: dict[str, Any] = {
        "source": "greenhouse",
        "source_job_id": "8653419002",
        "company_id": company_id,
        "title": "Senior Backend Engineer",
        "job_url": "https://careers.example.com/jobs/1",
        "first_seen_at": OBSERVED,
        "last_seen_at": OBSERVED,
        "raw_data": '{"id": 8653419002}',
        "content_hash": "abc123",
    }
    params.update(overrides)
    columns = list(params)
    placeholders = ", ".join(
        "CAST(:raw_data AS jsonb)" if column == "raw_data" else f":{column}"
        for column in columns
    )
    return int(
        connection.execute(
            text(
                f"INSERT INTO jobs ({', '.join(columns)}) "  # noqa: S608
                f"VALUES ({placeholders}) RETURNING id"
            ),
            params,
        ).scalar_one()
    )


def insert_run(
    connection: Connection,
    *,
    status: str = "running",
    **overrides: Any,  # noqa: ANN401
) -> int:
    params: dict[str, Any] = {"status": status, "started_at": OBSERVED}
    columns = ["status", "started_at"]
    for key, value in overrides.items():
        params[key] = value
        columns.append(key)
    placeholders = ", ".join(f":{c}" for c in columns)
    return int(
        connection.execute(
            text(
                f"INSERT INTO crawl_runs ({', '.join(columns)}) "  # noqa: S608
                f"VALUES ({placeholders}) RETURNING id"
            ),
            params,
        ).scalar_one()
    )


def insert_result(
    connection: Connection,
    run_id: int,
    company_id: int,
    *,
    status: str = "success",
    **overrides: Any,  # noqa: ANN401
) -> int:
    params: dict[str, Any] = {
        "crawl_run_id": run_id,
        "company_id": company_id,
        "status": status,
        "started_at": OBSERVED,
    }
    columns = list(params)
    for key, value in overrides.items():
        params[key] = value
        columns.append(key)
    placeholders = ", ".join(f":{c}" for c in columns)
    return int(
        connection.execute(
            text(
                f"INSERT INTO crawl_company_results ({', '.join(columns)}) "  # noqa: S608
                f"VALUES ({placeholders}) RETURNING id"
            ),
            params,
        ).scalar_one()
    )


# --- structure --------------------------------------------------------------


def test_all_four_tables_exist(schema_engine: Engine) -> None:
    assert set(inspect(schema_engine).get_table_names()) >= EXPECTED_TABLES


@pytest.mark.parametrize(
    ("table", "column", "nullable"),
    [
        ("companies", "source", False),
        ("companies", "board_token", False),
        ("companies", "display_name", False),
        ("companies", "is_enabled", False),
        ("jobs", "source", False),
        ("jobs", "source_job_id", False),
        ("jobs", "company_id", False),
        ("jobs", "title", False),
        ("jobs", "job_url", False),
        ("jobs", "first_seen_at", False),
        ("jobs", "last_seen_at", False),
        ("jobs", "is_active", False),
        ("jobs", "raw_data", False),
        ("jobs", "content_hash", False),
        ("jobs", "location", True),
        ("jobs", "department", True),
        ("jobs", "description", True),
        ("jobs", "apply_url", True),
        ("jobs", "source_published_at", True),
        ("jobs", "source_updated_at", True),
        ("crawl_runs", "status", False),
        ("crawl_runs", "started_at", False),
        ("crawl_runs", "finished_at", True),
        ("crawl_company_results", "crawl_run_id", False),
        ("crawl_company_results", "company_id", False),
        ("crawl_company_results", "status", False),
        ("crawl_company_results", "error_type", True),
        ("crawl_company_results", "error_message", True),
    ],
)
def test_column_nullability(
    schema_engine: Engine, table: str, column: str, nullable: bool
) -> None:
    columns = {c["name"]: c for c in inspect(schema_engine).get_columns(table)}

    assert column in columns, f"{table}.{column} is missing"
    assert columns[column]["nullable"] is nullable


def test_observed_at_is_not_a_jobs_column(schema_engine: Engine) -> None:
    # SPEC: observed_at is a domain value, not a stored column.
    names = {c["name"] for c in inspect(schema_engine).get_columns("jobs")}

    assert "observed_at" not in names


@pytest.mark.parametrize(
    ("table", "column"),
    [
        ("jobs", "first_seen_at"),
        ("jobs", "last_seen_at"),
        ("jobs", "source_published_at"),
        ("jobs", "source_updated_at"),
        ("crawl_runs", "started_at"),
        ("crawl_company_results", "started_at"),
    ],
)
def test_timestamps_are_timezone_aware(
    schema_engine: Engine, table: str, column: str
) -> None:
    # ADR-0003: timestamptz, never naive timestamp.
    columns = {c["name"]: c for c in inspect(schema_engine).get_columns(table)}

    assert columns[column]["type"].timezone is True  # type: ignore[attr-defined]


def test_source_job_id_is_text(schema_engine: Engine) -> None:
    # ADR-0002: text, so UUID-based sources need no migration.
    columns = {c["name"]: c for c in inspect(schema_engine).get_columns("jobs")}

    assert columns["source_job_id"]["type"].python_type is str


def test_raw_data_is_jsonb(schema_engine: Engine) -> None:
    columns = {c["name"]: c for c in inspect(schema_engine).get_columns("jobs")}

    assert type(columns["raw_data"]["type"]).__name__ == "JSONB"


# --- identity ---------------------------------------------------------------


def test_company_identity_is_unique(connection: Connection) -> None:
    insert_company(connection)

    with pytest.raises(IntegrityError):
        insert_company(connection)


def test_company_identity_is_source_scoped(connection: Connection) -> None:
    insert_company(connection, source="greenhouse", board_token="acme")
    insert_company(connection, source="lever", board_token="acme")

    count = connection.execute(
        text("SELECT count(*) FROM companies WHERE board_token = 'acme'")
    ).scalar_one()

    assert count == 2


def test_display_name_is_mutable(connection: Connection) -> None:
    company_id = insert_company(connection)

    connection.execute(
        text("UPDATE companies SET display_name = :name WHERE id = :id"),
        {"name": "ExampleCorp International", "id": company_id},
    )
    renamed = connection.execute(
        text("SELECT display_name FROM companies WHERE id = :id"), {"id": company_id}
    ).scalar_one()

    assert renamed == "ExampleCorp International"


def test_job_identity_is_unique(connection: Connection) -> None:
    # The invariant the whole idempotency design rests on.
    company_id = insert_company(connection)
    insert_job(connection, company_id)

    with pytest.raises(IntegrityError):
        insert_job(connection, company_id)


def test_same_source_job_id_under_different_sources_is_allowed(
    connection: Connection,
) -> None:
    # The key is composite, not source_job_id alone.
    greenhouse = insert_company(connection, source="greenhouse", board_token="acme")
    lever = insert_company(connection, source="lever", board_token="acme")
    insert_job(connection, greenhouse, source="greenhouse", source_job_id="123")
    insert_job(connection, lever, source="lever", source_job_id="123")

    count = connection.execute(
        text("SELECT count(*) FROM jobs WHERE source_job_id = '123'")
    ).scalar_one()

    assert count == 2


def test_company_result_is_unique_per_run(connection: Connection) -> None:
    company_id = insert_company(connection)
    run_id = insert_run(connection)
    insert_result(connection, run_id, company_id)

    with pytest.raises(IntegrityError):
        insert_result(connection, run_id, company_id)


# --- defaults ---------------------------------------------------------------


def test_is_active_defaults_to_true(connection: Connection) -> None:
    # ADR-0004: ingested jobs are active in the MVP.
    company_id = insert_company(connection)
    job_id = insert_job(connection, company_id)

    active = connection.execute(
        text("SELECT is_active FROM jobs WHERE id = :id"), {"id": job_id}
    ).scalar_one()

    assert active is True


def test_company_is_enabled_defaults_to_true(connection: Connection) -> None:
    company_id = insert_company(connection)

    enabled = connection.execute(
        text("SELECT is_enabled FROM companies WHERE id = :id"), {"id": company_id}
    ).scalar_one()

    assert enabled is True


def test_counters_default_to_zero(connection: Connection) -> None:
    run_id = insert_run(connection)

    row = connection.execute(
        text(
            "SELECT jobs_fetched, jobs_new, jobs_existing, jobs_updated, jobs_invalid"
            " FROM crawl_runs WHERE id = :id"
        ),
        {"id": run_id},
    ).one()

    assert list(row) == [0, 0, 0, 0, 0]


# --- foreign keys -----------------------------------------------------------


def test_job_requires_an_existing_company(connection: Connection) -> None:
    with pytest.raises(IntegrityError):
        insert_job(connection, 999999)


def test_company_result_requires_an_existing_run(connection: Connection) -> None:
    company_id = insert_company(connection)

    with pytest.raises(IntegrityError):
        insert_result(connection, 999999, company_id)


def test_deleting_a_referenced_company_is_rejected(connection: Connection) -> None:
    # Historical referential integrity: a board leaving configuration is
    # disabled, never deleted (ADR-0012).
    company_id = insert_company(connection)
    insert_job(connection, company_id)

    with pytest.raises(IntegrityError):
        connection.execute(
            text("DELETE FROM companies WHERE id = :id"), {"id": company_id}
        )


def test_deleting_a_company_with_crawl_history_is_rejected(
    connection: Connection,
) -> None:
    company_id = insert_company(connection)
    run_id = insert_run(connection)
    insert_result(connection, run_id, company_id)

    with pytest.raises(IntegrityError):
        connection.execute(
            text("DELETE FROM companies WHERE id = :id"), {"id": company_id}
        )


def test_deleting_a_crawl_run_cascades_its_company_results(
    connection: Connection,
) -> None:
    company_id = insert_company(connection)
    run_id = insert_run(connection)
    insert_result(connection, run_id, company_id)

    connection.execute(text("DELETE FROM crawl_runs WHERE id = :id"), {"id": run_id})
    remaining = connection.execute(
        text("SELECT count(*) FROM crawl_company_results WHERE crawl_run_id = :id"),
        {"id": run_id},
    ).scalar_one()

    assert remaining == 0


# --- CHECK constraints ------------------------------------------------------


@pytest.mark.parametrize("status", ["running", "success", "partial_success", "failed"])
def test_crawl_run_accepts_every_documented_status(
    connection: Connection, status: str
) -> None:
    assert insert_run(connection, status=status)


@pytest.mark.parametrize("status", ["succeeded", "SUCCESS", "", "partial"])
def test_crawl_run_rejects_undocumented_status(
    connection: Connection, status: str
) -> None:
    with pytest.raises(IntegrityError):
        insert_run(connection, status=status)


@pytest.mark.parametrize("status", ["success", "failed"])
def test_company_result_accepts_every_documented_status(
    connection: Connection, status: str
) -> None:
    company_id = insert_company(connection)
    run_id = insert_run(connection)

    assert insert_result(connection, run_id, company_id, status=status)


@pytest.mark.parametrize("status", ["running", "partial_success", "SUCCESS", ""])
def test_company_result_rejects_undocumented_status(
    connection: Connection, status: str
) -> None:
    # A company either succeeded or failed; partial_success is a run-level concept.
    company_id = insert_company(connection)
    run_id = insert_run(connection)

    with pytest.raises(IntegrityError):
        insert_result(connection, run_id, company_id, status=status)


@pytest.mark.parametrize(
    "counter",
    [
        "companies_total",
        "companies_succeeded",
        "companies_failed",
        "jobs_fetched",
        "jobs_new",
        "jobs_existing",
        "jobs_updated",
        "jobs_invalid",
    ],
)
def test_crawl_run_rejects_negative_counters(
    connection: Connection, counter: str
) -> None:
    override: dict[str, Any] = {counter: -1}

    with pytest.raises(IntegrityError):
        insert_run(connection, **override)


@pytest.mark.parametrize(
    "counter",
    ["jobs_fetched", "jobs_new", "jobs_existing", "jobs_updated", "jobs_invalid"],
)
def test_company_result_rejects_negative_counters(
    connection: Connection, counter: str
) -> None:
    company_id = insert_company(connection)
    run_id = insert_run(connection)

    override: dict[str, Any] = {counter: -1}

    with pytest.raises(IntegrityError):
        insert_result(connection, run_id, company_id, **override)


# --- source_job_id may not be blank -----------------------------------------


def test_normal_source_job_id_is_accepted(connection: Connection) -> None:
    company_id = insert_company(connection)

    assert insert_job(connection, company_id, source_job_id="8653419002")


def test_empty_source_job_id_is_rejected(connection: Connection) -> None:
    # The domain model already refuses this; the database must too, because
    # identity depends on it and not every future write path is the ORM.
    company_id = insert_company(connection)

    with pytest.raises(IntegrityError):
        insert_job(connection, company_id, source_job_id="")


def test_whitespace_only_source_job_id_is_rejected(connection: Connection) -> None:
    company_id = insert_company(connection)

    with pytest.raises(IntegrityError):
        insert_job(connection, company_id, source_job_id="   ")


@pytest.mark.parametrize(
    "value", ["\t", "\n", " \t\n "], ids=["tab", "newline", "mixed"]
)
def test_non_space_whitespace_is_also_rejected(
    connection: Connection, value: str
) -> None:
    # A btrim()-based check with the default character list would accept these,
    # so the constraint must not be written that way.
    company_id = insert_company(connection)

    with pytest.raises(IntegrityError):
        insert_job(connection, company_id, source_job_id=value)


def test_padded_source_job_id_is_accepted_by_the_database(
    connection: Connection,
) -> None:
    # The invariant is "not blank", not "already trimmed". Trimming is the
    # normalizer's job; the database only rejects the absence of any content.
    company_id = insert_company(connection)

    assert insert_job(connection, company_id, source_job_id=" 123 ")


# --- round trips ------------------------------------------------------------


def test_jsonb_round_trips_nested_structures(connection: Connection) -> None:
    company_id = insert_company(connection)
    payload = '{"id": 1, "departments": [{"name": "Engineering", "child_ids": []}]}'
    job_id = insert_job(connection, company_id, raw_data=payload)

    stored = connection.execute(
        text("SELECT raw_data FROM jobs WHERE id = :id"), {"id": job_id}
    ).scalar_one()

    assert stored == {
        "id": 1,
        "departments": [{"name": "Engineering", "child_ids": []}],
    }


def test_jsonb_supports_key_access(connection: Connection) -> None:
    # Proves the column is really jsonb, not text holding JSON.
    company_id = insert_company(connection)
    job_id = insert_job(
        connection, company_id, raw_data='{"requisition_id": "R-01182"}'
    )

    value = connection.execute(
        text("SELECT raw_data ->> 'requisition_id' FROM jobs WHERE id = :id"),
        {"id": job_id},
    ).scalar_one()

    assert value == "R-01182"


def test_offset_aware_timestamp_round_trips_as_utc(connection: Connection) -> None:
    # Greenhouse emits offsets such as -04:00 (SPEC, observed behavior).
    company_id = insert_company(connection)
    moment = datetime(2026, 8, 21, 13, 2, 33, tzinfo=timezone(-timedelta(hours=4)))
    job_id = insert_job(connection, company_id, source_updated_at=moment)

    stored = connection.execute(
        text("SELECT source_updated_at FROM jobs WHERE id = :id"), {"id": job_id}
    ).scalar_one()

    assert stored == datetime(2026, 8, 21, 17, 2, 33, tzinfo=UTC)
    assert stored.tzinfo is not None


def test_null_source_timestamps_are_stored_as_null(connection: Connection) -> None:
    # ADR-0003: unavailable source timestamps stay null, never inferred.
    company_id = insert_company(connection)
    job_id = insert_job(connection, company_id)

    row = connection.execute(
        text("SELECT source_published_at, source_updated_at FROM jobs WHERE id = :id"),
        {"id": job_id},
    ).one()

    assert row.source_published_at is None
    assert row.source_updated_at is None


def test_naive_timestamp_is_interpreted_not_silently_dropped(
    connection: Connection,
) -> None:
    # timestamptz never stores a naive value verbatim; the column always
    # yields an aware datetime back.
    company_id = insert_company(connection)
    job_id = insert_job(connection, company_id)

    stored = connection.execute(
        text("SELECT first_seen_at FROM jobs WHERE id = :id"), {"id": job_id}
    ).scalar_one()

    assert stored.utcoffset() == timedelta(0)


# --- indexes ----------------------------------------------------------------


def test_expected_indexes_exist(schema_engine: Engine) -> None:
    inspector = inspect(schema_engine)
    indexed_columns = {
        tuple(index["column_names"]) for index in inspector.get_indexes("jobs")
    }

    assert ("company_id",) in indexed_columns
    assert ("first_seen_at",) in indexed_columns
    assert ("last_seen_at",) in indexed_columns


def test_company_result_lookup_index_exists(schema_engine: Engine) -> None:
    # Future deactivation asks "when did this company last crawl successfully?"
    inspector = inspect(schema_engine)
    indexed = {
        tuple(index["column_names"])
        for index in inspector.get_indexes("crawl_company_results")
    }

    assert ("company_id", "status", "finished_at") in indexed


def test_unexpected_write_errors_are_integrity_errors(connection: Connection) -> None:
    # Guards the guard: a typo'd column would raise DBAPIError, not
    # IntegrityError, so the constraint tests above cannot pass vacuously.
    with pytest.raises(DBAPIError) as caught:
        connection.execute(text("INSERT INTO jobs (nonexistent) VALUES (1)"))

    assert not isinstance(caught.value, IntegrityError)
