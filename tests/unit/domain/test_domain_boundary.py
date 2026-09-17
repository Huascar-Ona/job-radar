"""The domain must not depend on any source, transport or persistence library.

ARCHITECTURE states this boundary in prose; this test enforces it mechanically.
"""

import ast
from pathlib import Path

import pytest

import job_radar.domain

DOMAIN_ROOT = Path(job_radar.domain.__file__).parent

FORBIDDEN_PREFIXES = (
    "job_radar.collectors",
    "greenhouse",
    "sqlalchemy",
    "alembic",
    "psycopg",
    "httpx",
    "requests",
    "urllib",
    "http",
    "socket",
    "yaml",
)


def domain_modules() -> list[Path]:
    return sorted(DOMAIN_ROOT.rglob("*.py"))


def imported_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


def test_domain_package_is_not_empty() -> None:
    # Guards the guard: an empty package would make every check below vacuous.
    assert domain_modules()


@pytest.mark.parametrize("path", domain_modules(), ids=lambda p: p.name)
def test_module_imports_nothing_forbidden(path: Path) -> None:
    offenders = {
        name
        for name in imported_names(path)
        for prefix in FORBIDDEN_PREFIXES
        if name == prefix or name.startswith(f"{prefix}.")
    }

    assert not offenders, f"{path.name} imports forbidden modules: {sorted(offenders)}"


def test_no_greenhouse_field_names_appear_in_the_domain() -> None:
    # Field names observed only in the Greenhouse payload must not leak here.
    greenhouse_only = (
        "absolute_url",
        "first_published",
        "internal_job_id",
        "requisition_id",
        "board_token",
        "offices",
    )
    for path in domain_modules():
        text = path.read_text(encoding="utf-8")
        for name in greenhouse_only:
            assert name not in text, f"{path.name} references Greenhouse field {name!r}"


# --- the persistence layer must also stay source-agnostic -------------------

import job_radar.persistence  # noqa: E402

PERSISTENCE_ROOT = Path(job_radar.persistence.__file__).parent

# Field names that exist only in a Greenhouse payload. Naming the provider in
# a comment that explains a decision is fine; carrying its field names is not.
# Dependency on the provider package is covered by the import test below.
GREENHOUSE_ONLY_NAMES = (
    "absolute_url",
    "first_published",
    "internal_job_id",
    "requisition_id",
    "boards-api",
)


def persistence_modules() -> list[Path]:
    return sorted(PERSISTENCE_ROOT.rglob("*.py"))


def test_persistence_package_is_not_empty() -> None:
    assert persistence_modules()


@pytest.mark.parametrize("path", persistence_modules(), ids=lambda p: p.name)
def test_persistence_imports_no_source_package(path: Path) -> None:
    offenders = {
        name
        for name in imported_names(path)
        for prefix in ("job_radar.collectors", "greenhouse", "httpx", "requests")
        if name == prefix or name.startswith(f"{prefix}.")
    }

    assert not offenders, f"{path.name} imports {sorted(offenders)}"


@pytest.mark.parametrize("path", persistence_modules(), ids=lambda p: p.name)
def test_persistence_uses_no_greenhouse_field_names(path: Path) -> None:
    text_content = path.read_text(encoding="utf-8").lower()
    for name in GREENHOUSE_ONLY_NAMES:
        assert name not in text_content, f"{path.name} references {name!r}"
