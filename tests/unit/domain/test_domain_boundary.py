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
    "job_radar.ingestion",
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


# --- collectors may reach the network, never the database -------------------

import job_radar.collectors  # noqa: E402

COLLECTORS_ROOT = Path(job_radar.collectors.__file__).parent


def collector_modules() -> list[Path]:
    return sorted(COLLECTORS_ROOT.rglob("*.py"))


def test_collectors_package_is_not_empty() -> None:
    assert collector_modules()


@pytest.mark.parametrize("path", collector_modules(), ids=lambda p: p.name)
def test_collectors_import_no_persistence(path: Path) -> None:
    # A collector that could write would make transaction ownership a fiction
    # (ADR-0016). httpx is expected here; SQLAlchemy is not.
    offenders = {
        name
        for name in imported_names(path)
        for prefix in ("sqlalchemy", "alembic", "psycopg", "job_radar.persistence")
        if name == prefix or name.startswith(f"{prefix}.")
    }

    assert not offenders, f"{path.name} imports {sorted(offenders)}"


# --- Greenhouse schemas stay inside the Greenhouse package ------------------

import job_radar  # noqa: E402

SOURCE_ROOT = Path(job_radar.__file__).parent
GREENHOUSE_ROOT = COLLECTORS_ROOT / "greenhouse"
GREENHOUSE_SCHEMA = "job_radar.collectors.greenhouse.schema"


def imported_modules_and_members(path: Path) -> set[str]:
    """Like imported_names, plus ``module.member`` for each from-import.

    ``from job_radar.collectors.greenhouse import schema`` names the module
    only as a member, which imported_names alone would miss.
    """
    names = imported_names(path)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def modules_outside_greenhouse() -> list[Path]:
    return sorted(
        path
        for path in SOURCE_ROOT.rglob("*.py")
        if GREENHOUSE_ROOT not in path.parents
    )


def test_greenhouse_schema_module_exists() -> None:
    # Guards the guard: a renamed schema module would make the check vacuous.
    assert (GREENHOUSE_ROOT / "schema.py").is_file()
    assert modules_outside_greenhouse()


@pytest.mark.parametrize(
    "path",
    modules_outside_greenhouse(),
    ids=lambda p: str(p.relative_to(SOURCE_ROOT)),
)
def test_greenhouse_schema_is_imported_only_inside_its_package(path: Path) -> None:
    # ARCHITECTURE: Greenhouse Pydantic schemas are imported by nothing
    # outside the Greenhouse package.
    offenders = {
        name
        for name in imported_modules_and_members(path)
        if name == GREENHOUSE_SCHEMA or name.startswith(f"{GREENHOUSE_SCHEMA}.")
    }

    assert not offenders, f"{path.name} imports {sorted(offenders)}"


# --- the normalizer is pure translation -------------------------------------

# The normalizer and the source schema it validates with: together, the whole
# normalization path. The collector in the same package legitimately uses httpx.
NORMALIZATION_MODULES = (
    GREENHOUSE_ROOT / "normalizer.py",
    GREENHOUSE_ROOT / "schema.py",
)

# Transport, persistence, filesystem and time: a normalizer needs none of them
# (ADR-0016). The observation instant is an argument, never read here.
NORMALIZER_FORBIDDEN_PREFIXES = (
    "httpx",
    "requests",
    "urllib",
    "http",
    "socket",
    "sqlalchemy",
    "alembic",
    "psycopg",
    "job_radar.persistence",
    "job_radar.domain.clock",
    "pathlib",
    "os",
    "io",
    "shutil",
    "tempfile",
    "time",
)

NORMALIZER_FORBIDDEN_CALLS = frozenset({"open", "now", "utcnow", "today"})


@pytest.mark.parametrize("path", NORMALIZATION_MODULES, ids=lambda p: p.name)
def test_normalization_module_exists(path: Path) -> None:
    # Guards the guard: a renamed module would make the checks below vacuous.
    assert path.is_file()


@pytest.mark.parametrize("path", NORMALIZATION_MODULES, ids=lambda p: p.name)
def test_normalization_imports_no_io_or_clock(path: Path) -> None:
    # imported_modules_and_members, so that `from job_radar.domain import
    # clock` is caught as well as `import job_radar.domain.clock`.
    offenders = {
        name
        for name in imported_modules_and_members(path)
        for prefix in NORMALIZER_FORBIDDEN_PREFIXES
        if name == prefix or name.startswith(f"{prefix}.")
    }

    assert not offenders, f"{path.name} imports {sorted(offenders)}"


@pytest.mark.parametrize("path", NORMALIZATION_MODULES, ids=lambda p: p.name)
def test_normalization_calls_no_open_or_clock(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    called: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                called.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                called.add(node.func.attr)

    assert not called & NORMALIZER_FORBIDDEN_CALLS


# --- ingestion orchestrates; it does not know sources or SQL ----------------

import job_radar.ingestion  # noqa: E402

INGESTION_ROOT = Path(job_radar.ingestion.__file__).parent

# SPEC "Ingestion run": the service is source-agnostic. Source wiring and the
# SQLAlchemy adapter are supplied by the caller, so nothing here may import a
# source package, transport, or the persistence layer (which would bring
# SQLAlchemy with it).
INGESTION_FORBIDDEN_PREFIXES = (
    "job_radar.collectors.greenhouse",
    "greenhouse",
    "httpx",
    "requests",
    "urllib",
    "http",
    "socket",
    "sqlalchemy",
    "alembic",
    "psycopg",
    "job_radar.persistence",
)


def ingestion_modules() -> list[Path]:
    return sorted(INGESTION_ROOT.rglob("*.py"))


def test_ingestion_package_has_its_modules() -> None:
    # Guards the guard: a renamed service would make the checks vacuous.
    names = {path.name for path in ingestion_modules()}
    assert {"service.py", "ports.py", "summary.py"} <= names


@pytest.mark.parametrize("path", ingestion_modules(), ids=lambda p: p.name)
def test_ingestion_imports_no_source_transport_or_sql(path: Path) -> None:
    offenders = {
        name
        for name in imported_modules_and_members(path)
        for prefix in INGESTION_FORBIDDEN_PREFIXES
        if name == prefix or name.startswith(f"{prefix}.")
    }

    assert not offenders, f"{path.name} imports {sorted(offenders)}"


@pytest.mark.parametrize("path", ingestion_modules(), ids=lambda p: p.name)
def test_ingestion_never_commits_or_rolls_back(path: Path) -> None:
    # Transactions are opened through the injected store factory; committing
    # and rolling back belong to the persistence adapter.
    source = path.read_text(encoding="utf-8")

    assert ".commit(" not in source
    assert ".rollback(" not in source
