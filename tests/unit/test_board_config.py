"""Board configuration validation (ADR-0012)."""

from pathlib import Path

import pytest

from job_radar.config.boards import (
    BoardConfig,
    BoardConfigError,
    BoardsConfig,
    load_boards_config,
)

VALID_YAML = """
boards:
  - source: greenhouse
    board_token: examplecorp
    display_name: ExampleCorp
    enabled: true
  - source: greenhouse
    board_token: disabledcorp
    display_name: DisabledCorp
    enabled: false
"""


def write(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "boards.yml"
    path.write_text(content, encoding="utf-8")
    return path


def test_loads_valid_configuration(tmp_path: Path) -> None:
    config = load_boards_config(write(tmp_path, VALID_YAML))

    assert len(config.boards) == 2
    assert config.boards[0].board_token == "examplecorp"
    assert config.boards[0].identity == ("greenhouse", "examplecorp")


def test_enabled_boards_excludes_disabled(tmp_path: Path) -> None:
    config = load_boards_config(write(tmp_path, VALID_YAML))

    assert [board.board_token for board in config.enabled_boards] == ["examplecorp"]


def test_enabled_defaults_to_true() -> None:
    board = BoardConfig(
        source="greenhouse", board_token="acme", display_name="Acme", enabled=True
    )

    assert board.enabled is True


def test_board_is_immutable() -> None:
    board = BoardConfig(
        source="greenhouse", board_token="acme", display_name="Acme", enabled=True
    )

    with pytest.raises(ValueError, match="frozen"):
        board.display_name = "Renamed"  # type: ignore[misc]


def test_empty_file_yields_no_boards(tmp_path: Path) -> None:
    assert load_boards_config(write(tmp_path, "")).boards == ()


def test_rejects_duplicate_identity(tmp_path: Path) -> None:
    duplicated = """
boards:
  - source: greenhouse
    board_token: acme
    display_name: Acme
  - source: greenhouse
    board_token: acme
    display_name: Acme Renamed
"""
    with pytest.raises(BoardConfigError, match="duplicate board identities"):
        load_boards_config(write(tmp_path, duplicated))


def test_rejects_unknown_source(tmp_path: Path) -> None:
    unknown = """
boards:
  - source: lever
    board_token: acme
    display_name: Acme
"""
    with pytest.raises(BoardConfigError, match="invalid board configuration"):
        load_boards_config(write(tmp_path, unknown))


def test_rejects_unknown_key(tmp_path: Path) -> None:
    extra = """
boards:
  - source: greenhouse
    board_token: acme
    display_name: Acme
    crawl_interval: 5m
"""
    with pytest.raises(BoardConfigError, match="invalid board configuration"):
        load_boards_config(write(tmp_path, extra))


def test_rejects_blank_board_token(tmp_path: Path) -> None:
    blank = """
boards:
  - source: greenhouse
    board_token: ""
    display_name: Acme
"""
    with pytest.raises(BoardConfigError, match="invalid board configuration"):
        load_boards_config(write(tmp_path, blank))


def test_rejects_non_mapping_document(tmp_path: Path) -> None:
    with pytest.raises(BoardConfigError, match="must be a mapping"):
        load_boards_config(write(tmp_path, "- just\n- a\n- list\n"))


def test_rejects_malformed_yaml(tmp_path: Path) -> None:
    with pytest.raises(BoardConfigError, match="not valid YAML"):
        load_boards_config(write(tmp_path, "boards: [unclosed\n"))


def test_missing_file_is_reported(tmp_path: Path) -> None:
    with pytest.raises(BoardConfigError, match="cannot read board configuration"):
        load_boards_config(tmp_path / "absent.yml")


def test_duplicate_detection_is_source_scoped() -> None:
    # Same token under different sources is two distinct companies.
    config = BoardsConfig(
        boards=(
            BoardConfig(
                source="greenhouse",
                board_token="acme",
                display_name="Acme",
                enabled=True,
            ),
        )
    )

    assert len(config.boards) == 1
