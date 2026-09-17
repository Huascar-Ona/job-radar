"""CompanyRepository: reconciling config/boards.yml into the companies table."""

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from job_radar.domain.job import JobSource
from job_radar.persistence.company_repository import CompanyRepository

pytestmark = pytest.mark.integration

DEFAULT_TOKEN = "examplecorp"


@pytest.fixture
def repository(session: Session) -> CompanyRepository:
    return CompanyRepository(session)


def fetch(session: Session, token: str = DEFAULT_TOKEN) -> object:
    return session.execute(
        text("SELECT * FROM companies WHERE board_token = :t"), {"t": token}
    ).one()


def count(session: Session) -> int:
    return int(session.execute(text("SELECT count(*) FROM companies")).scalar_one())


def test_sync_creates_the_configured_company(
    repository: CompanyRepository, session: Session
) -> None:
    company_id = repository.sync(
        source=JobSource.GREENHOUSE,
        board_token="examplecorp",
        display_name="ExampleCorp",
        enabled=True,
    )

    assert company_id
    assert fetch(session).display_name == "ExampleCorp"  # type: ignore[attr-defined]


def test_repeated_sync_is_idempotent(
    repository: CompanyRepository, session: Session
) -> None:
    first = repository.sync(
        source=JobSource.GREENHOUSE,
        board_token="examplecorp",
        display_name="ExampleCorp",
        enabled=True,
    )
    second = repository.sync(
        source=JobSource.GREENHOUSE,
        board_token="examplecorp",
        display_name="ExampleCorp",
        enabled=True,
    )

    assert first == second
    assert count(session) == 1


def test_display_name_may_change(
    repository: CompanyRepository, session: Session
) -> None:
    # Mutable metadata: a rename is an update, never a new company (ADR-0007).
    company_id = repository.sync(
        source=JobSource.GREENHOUSE,
        board_token="examplecorp",
        display_name="ExampleCorp",
        enabled=True,
    )

    renamed = repository.sync(
        source=JobSource.GREENHOUSE,
        board_token="examplecorp",
        display_name="ExampleCorp International",
        enabled=True,
    )

    assert renamed == company_id
    assert fetch(session).display_name == "ExampleCorp International"  # type: ignore[attr-defined]
    assert count(session) == 1


def test_enabled_state_may_change(
    repository: CompanyRepository, session: Session
) -> None:
    repository.sync(
        source=JobSource.GREENHOUSE,
        board_token="examplecorp",
        display_name="ExampleCorp",
        enabled=True,
    )

    repository.sync(
        source=JobSource.GREENHOUSE,
        board_token="examplecorp",
        display_name="ExampleCorp",
        enabled=False,
    )

    assert fetch(session).is_enabled is False  # type: ignore[attr-defined]


def test_identity_is_source_and_board_token(
    repository: CompanyRepository, session: Session
) -> None:
    greenhouse = repository.sync(
        source=JobSource.GREENHOUSE,
        board_token="acme",
        display_name="Acme",
        enabled=True,
    )
    lever = repository.sync(
        source="lever",
        board_token="acme",
        display_name="Acme",
        enabled=True,
    )

    assert greenhouse != lever
    assert count(session) == 2


def test_absent_boards_are_disabled_not_deleted(
    repository: CompanyRepository, session: Session
) -> None:
    # ADR-0012: history keeps its foreign keys, so rows are never removed.
    kept = repository.sync(
        source=JobSource.GREENHOUSE,
        board_token="kept",
        display_name="Kept",
        enabled=True,
    )
    dropped = repository.sync(
        source=JobSource.GREENHOUSE,
        board_token="dropped",
        display_name="Dropped",
        enabled=True,
    )

    repository.disable_absent(source=JobSource.GREENHOUSE, board_tokens=["kept"])

    assert count(session) == 2
    assert fetch(session, "kept").is_enabled is True  # type: ignore[attr-defined]
    assert fetch(session, "dropped").is_enabled is False  # type: ignore[attr-defined]
    assert kept != dropped


def test_disable_absent_does_not_touch_other_sources(
    repository: CompanyRepository, session: Session
) -> None:
    repository.sync(
        source="lever", board_token="acme", display_name="Acme", enabled=True
    )

    repository.disable_absent(source=JobSource.GREENHOUSE, board_tokens=[])

    assert fetch(session, "acme").is_enabled is True  # type: ignore[attr-defined]


def test_enabled_companies_lists_only_enabled(
    repository: CompanyRepository,
) -> None:
    repository.sync(
        source=JobSource.GREENHOUSE,
        board_token="on",
        display_name="On",
        enabled=True,
    )
    repository.sync(
        source=JobSource.GREENHOUSE,
        board_token="off",
        display_name="Off",
        enabled=False,
    )

    tokens = [company.board_token for company in repository.enabled_companies()]

    assert tokens == ["on"]
