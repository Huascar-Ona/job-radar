"""The job query's construction, the read-only engine, and DatabaseSettings.

No database: SQL is compiled with the PostgreSQL dialect, not executed. What
PostgreSQL does with it (matching, ordering, NULLs, read-only enforcement)
is tested in tests/integration/test_job_query.py.
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from psycopg import IsolationLevel
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql

import job_radar.persistence.job_query as job_query_module
from job_radar.persistence.job_query import JobCriteria, _statement
from job_radar.persistence.session import create_read_only_engine
from job_radar.settings import DatabaseSettings, Settings

INSTANT = datetime(2026, 11, 12, tzinfo=UTC)


def compiled(criteria: JobCriteria) -> tuple[str, dict[str, object]]:
    dialect = postgresql.dialect()  # type: ignore[no-untyped-call]
    statement = _statement(criteria).compile(dialect=dialect)
    return " ".join(str(statement).split()), dict(statement.params)


# --- criteria -------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        {"first_seen_since": INSTANT, "first_seen_at": INSTANT},
        {"first_seen_since": INSTANT.replace(tzinfo=None)},
        {"first_seen_at": INSTANT.replace(tzinfo=None)},
        {"title": ""},
        {"company": ""},
        {"limit": 0},
        {"limit": 1001},
    ],
)
def test_invalid_criteria_are_rejected(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError, match=r"exclusive|aware|empty|limit"):
        JobCriteria(**kwargs)  # type: ignore[arg-type]


def test_default_criteria_list_fifty() -> None:
    assert JobCriteria().limit == 50


# --- statement ------------------------------------------------------------------


def test_only_active_jobs_in_a_total_order() -> None:
    sql, _ = compiled(JobCriteria())

    assert "jobs.is_active IS true" in sql
    assert sql.endswith("ORDER BY jobs.first_seen_at DESC, jobs.id DESC")
    assert "JOIN companies ON companies.id = jobs.company_id" in sql
    for excluded in ("raw_data", "description", "content_hash"):
        assert excluded not in sql


def test_since_is_inclusive_and_run_correlation_is_exact() -> None:
    since_sql, since_params = compiled(JobCriteria(first_seen_since=INSTANT))
    at_sql, at_params = compiled(JobCriteria(first_seen_at=INSTANT))

    assert "jobs.first_seen_at >= %(first_seen_at_1)s" in since_sql
    assert since_params["first_seen_at_1"] == INSTANT
    assert "jobs.first_seen_at = %(first_seen_at_1)s" in at_sql
    assert at_params["first_seen_at_1"] == INSTANT


def test_text_filters_escape_wildcards_and_ignore_case() -> None:
    sql, params = compiled(
        JobCriteria(title="50%_off/\\", location="Mexico", department="Data")
    )

    assert "jobs.title ILIKE '%%' || %(title_1)s || '%%' ESCAPE '/'" in sql
    assert params["title_1"] == "50/%/_off//\\"
    assert "jobs.location ILIKE" in sql
    assert params["location_1"] == "Mexico"
    assert "jobs.department ILIKE" in sql


def test_company_matches_display_name_or_board_token() -> None:
    sql, params = compiled(JobCriteria(company="acme"))

    assert (
        "((companies.display_name ILIKE '%%' || %(display_name_1)s || '%%'"
        " ESCAPE '/') OR (companies.board_token ILIKE '%%' || %(board_token_1)s"
        " || '%%' ESCAPE '/'))"
    ) in sql
    assert params["display_name_1"] == params["board_token_1"] == "acme"


def test_the_query_module_never_commits_or_rolls_back() -> None:
    # ARCHITECTURE, "Transaction ownership": statements only.
    source = Path(job_query_module.__file__).read_text(encoding="utf-8")

    assert ".commit()" not in source
    assert ".rollback()" not in source


# --- read-only engine -----------------------------------------------------------


class FakeDbapiConnection:
    isolation_level: object = None
    read_only: object = None


def test_read_only_engine_marks_every_new_connection() -> None:
    engine = create_read_only_engine("postgresql+psycopg://u:p@localhost/db")
    try:
        # Only this module's listener; the dialect's own needs a real driver.
        [listener] = [
            fn for fn in engine.pool.dispatch.connect if fn.__name__ == "_read_only"
        ]
        connection = FakeDbapiConnection()
        listener(connection, None)
    finally:
        engine.dispose()

    assert connection.isolation_level == IsolationLevel.REPEATABLE_READ
    assert connection.read_only is True


# --- settings -------------------------------------------------------------------


def test_database_settings_ignore_ingestion_settings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("JOB_RADAR_HTTP_TIMEOUT_SECONDS", "-1")
    monkeypatch.setenv("JOB_RADAR_BOARDS_CONFIG_PATH", str(tmp_path / "missing"))
    env_file = tmp_path / ".env"
    env_file.write_text(
        "JOB_RADAR_DATABASE_URL=postgresql+psycopg://u:p@db.example/x\n"
        "JOB_RADAR_HTTP_TIMEOUT_SECONDS=not-a-number\n",
        encoding="utf-8",
    )

    settings = DatabaseSettings(_env_file=env_file)

    assert settings.database_url == "postgresql+psycopg://u:p@db.example/x"
    assert set(type(settings).model_fields) == {"database_url"}


def test_ingestion_settings_still_validate_the_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JOB_RADAR_HTTP_TIMEOUT_SECONDS", "-1")

    with pytest.raises(ValidationError):
        Settings()


def test_settings_keep_their_fields_sources_and_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JOB_RADAR_DATABASE_URL", "postgresql+psycopg://e:e@env/x")

    settings = Settings()

    assert isinstance(settings, DatabaseSettings)
    assert list(Settings.model_fields) == [
        "database_url",
        "boards_config_path",
        "http_timeout_seconds",
    ]
    assert settings.database_url == "postgresql+psycopg://e:e@env/x"
    assert settings.boards_config_path == Path("config/boards.yml")
    assert settings.http_timeout_seconds == 10.0
