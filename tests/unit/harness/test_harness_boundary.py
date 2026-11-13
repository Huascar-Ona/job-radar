"""H1 stays out of Job Radar, and Job Radar stays out of H1 (D1)."""

import ast
from pathlib import Path

import pytest

import job_radar
import tools.harness_metrics

APP_ROOT = Path(job_radar.__file__).parent
HARNESS_ROOT = Path(tools.harness_metrics.__file__).parent


def imported(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_both_trees_exist() -> None:
    assert list(APP_ROOT.rglob("*.py"))
    assert list(HARNESS_ROOT.rglob("*.py"))


@pytest.mark.parametrize(
    "path", sorted(HARNESS_ROOT.rglob("*.py")), ids=lambda p: p.name
)
def test_harness_does_not_import_the_application(path: Path) -> None:
    assert not {n for n in imported(path) if n.split(".")[0] == "job_radar"}


@pytest.mark.parametrize(
    "path",
    sorted(APP_ROOT.rglob("*.py")),
    ids=lambda p: str(p.relative_to(APP_ROOT)),
)
def test_application_does_not_import_the_harness(path: Path) -> None:
    assert not {n for n in imported(path) if n.split(".")[0] == "tools"}


@pytest.mark.parametrize(
    "path", sorted(HARNESS_ROOT.rglob("*.py")), ids=lambda p: p.name
)
def test_harness_uses_the_standard_library_only(path: Path) -> None:
    # D1: no new dependency; tomllib, json and argparse are enough.
    third_party = {"sqlalchemy", "pydantic", "httpx", "yaml", "psycopg", "alembic"}
    assert not {n for n in imported(path) if n.split(".")[0] in third_party}
