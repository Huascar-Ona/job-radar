"""The checked-in configuration and packaging metadata must be usable."""

from pathlib import Path

from job_radar import __version__
from job_radar.cli import main
from job_radar.config.boards import load_boards_config
from job_radar.settings import Settings


def test_shipped_boards_config_is_valid(project_root: Path) -> None:
    config = load_boards_config(project_root / "config" / "boards.yml")

    assert config.boards, "config/boards.yml should ship a usable example"
    assert all(board.source == "greenhouse" for board in config.boards)


def test_settings_default_to_psycopg_driver() -> None:
    assert Settings().database_url.startswith("postgresql+psycopg://")


def test_http_timeout_is_bounded() -> None:
    # ADR-0009: requests must never be unbounded.
    assert Settings().http_timeout_seconds > 0


def test_cli_entry_point_succeeds() -> None:
    assert main() == 0


def test_version_is_exposed() -> None:
    assert __version__ == "0.1.0"
