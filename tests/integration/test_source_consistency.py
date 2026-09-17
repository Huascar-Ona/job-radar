"""A job's source must match the source of the company it belongs to.

Without this, nothing stops a Lever job from being attached to a Greenhouse
company: two columns that must agree, with no constraint making them agree.
Every case here is driven with raw SQL, so a pass proves PostgreSQL rejects
the write rather than that the ORM declined to send it.
"""

import pytest
from sqlalchemy import Connection, Engine, inspect, text
from sqlalchemy.exc import IntegrityError

from tests.integration.test_schema import insert_company, insert_job

pytestmark = pytest.mark.integration


# --- the constraint holds ---------------------------------------------------


def test_greenhouse_job_into_greenhouse_company_succeeds(
    connection: Connection,
) -> None:
    company_id = insert_company(connection, source="greenhouse", board_token="acme")

    assert insert_job(connection, company_id, source="greenhouse")


def test_lever_job_into_lever_company_succeeds(connection: Connection) -> None:
    company_id = insert_company(connection, source="lever", board_token="acme")

    assert insert_job(connection, company_id, source="lever")


def test_lever_job_into_greenhouse_company_is_rejected(connection: Connection) -> None:
    company_id = insert_company(connection, source="greenhouse", board_token="acme")

    with pytest.raises(IntegrityError):
        insert_job(connection, company_id, source="lever")


def test_greenhouse_job_into_lever_company_is_rejected(connection: Connection) -> None:
    company_id = insert_company(connection, source="lever", board_token="acme")

    with pytest.raises(IntegrityError):
        insert_job(connection, company_id, source="greenhouse")


def test_changing_a_job_source_away_from_its_company_is_rejected(
    connection: Connection,
) -> None:
    # The constraint must hold on UPDATE, not only on INSERT.
    company_id = insert_company(connection, source="greenhouse", board_token="acme")
    job_id = insert_job(connection, company_id, source="greenhouse")

    with pytest.raises(IntegrityError):
        connection.execute(
            text("UPDATE jobs SET source = 'lever' WHERE id = :id"), {"id": job_id}
        )


def test_changing_a_company_source_under_its_jobs_is_rejected(
    connection: Connection,
) -> None:
    # RESTRICT applies to the referenced key, so a company cannot change source
    # out from under jobs that point at it.
    company_id = insert_company(connection, source="greenhouse", board_token="acme")
    insert_job(connection, company_id, source="greenhouse")

    with pytest.raises(IntegrityError):
        connection.execute(
            text("UPDATE companies SET source = 'lever' WHERE id = :id"),
            {"id": company_id},
        )


# --- previously established behavior is preserved ---------------------------


def test_deleting_a_referenced_company_is_still_rejected(
    connection: Connection,
) -> None:
    # RESTRICT survived the foreign key being replaced.
    company_id = insert_company(connection, source="greenhouse", board_token="acme")
    insert_job(connection, company_id, source="greenhouse")

    with pytest.raises(IntegrityError):
        connection.execute(
            text("DELETE FROM companies WHERE id = :id"), {"id": company_id}
        )


def test_job_identity_constraint_is_unchanged(schema_engine: Engine) -> None:
    # UNIQUE (source, source_job_id) must survive the migration untouched.
    constraints = {
        c["name"]: tuple(c["column_names"])
        for c in inspect(schema_engine).get_unique_constraints("jobs")
    }

    assert constraints["uq_jobs_source_job_id"] == ("source", "source_job_id")


def test_company_identity_constraint_is_unchanged(schema_engine: Engine) -> None:
    constraints = {
        c["name"]: tuple(c["column_names"])
        for c in inspect(schema_engine).get_unique_constraints("companies")
    }

    assert constraints["uq_companies_source_token"] == ("source", "board_token")


# --- schema shape -----------------------------------------------------------


def test_companies_has_the_referenced_unique_key(schema_engine: Engine) -> None:
    # The composite foreign key needs (id, source) to be a unique key.
    constraints = {
        c["name"]: tuple(c["column_names"])
        for c in inspect(schema_engine).get_unique_constraints("companies")
    }

    assert constraints["uq_companies_id_source"] == ("id", "source")


def test_jobs_company_foreign_key_is_composite(schema_engine: Engine) -> None:
    keys = inspect(schema_engine).get_foreign_keys("jobs")
    company_keys = [k for k in keys if k["referred_table"] == "companies"]

    assert len(company_keys) == 1, "exactly one foreign key to companies"
    assert tuple(company_keys[0]["constrained_columns"]) == ("company_id", "source")
    assert tuple(company_keys[0]["referred_columns"]) == ("id", "source")


def test_jobs_company_foreign_key_still_restricts(connection: Connection) -> None:
    key = connection.execute(
        text(
            "SELECT confdeltype FROM pg_constraint"
            " WHERE conname = 'fk_jobs_company_id_source_companies'"
        )
    ).scalar_one()

    assert key == "r", "ON DELETE RESTRICT"
