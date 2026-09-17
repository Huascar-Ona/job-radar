"""Board configuration: parsing and validation of ``config/boards.yml``.

This module validates configuration only. It performs no I/O against job
sources and holds no ingestion behavior (ADR-0012).
"""

from pathlib import Path
from typing import Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from job_radar.domain.job import JobSource


class BoardConfigError(Exception):
    """Raised when board configuration is missing, unreadable or invalid."""


class BoardConfig(BaseModel):
    """One configured job board.

    Identity is ``(source, board_token)``; ``display_name`` is mutable
    metadata and carries no identity (ADR-0007).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    source: JobSource
    board_token: str = Field(min_length=1, pattern=r"^[A-Za-z0-9_-]+$")
    display_name: str = Field(min_length=1)
    enabled: bool = True

    @property
    def identity(self) -> tuple[JobSource, str]:
        """The stable company identity for this board."""
        return (self.source, self.board_token)


class BoardsConfig(BaseModel):
    """The full contents of the board configuration file."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    boards: tuple[BoardConfig, ...] = ()

    @model_validator(mode="after")
    def _reject_duplicate_identities(self) -> Self:
        seen: set[tuple[JobSource, str]] = set()
        duplicates: list[str] = []
        for board in self.boards:
            if board.identity in seen:
                duplicates.append(f"{board.source}:{board.board_token}")
            seen.add(board.identity)
        if duplicates:
            joined = ", ".join(sorted(set(duplicates)))
            msg = f"duplicate board identities: {joined}"
            raise ValueError(msg)
        return self

    @property
    def enabled_boards(self) -> tuple[BoardConfig, ...]:
        """Boards that should be crawled."""
        return tuple(board for board in self.boards if board.enabled)


def load_boards_config(path: Path) -> BoardsConfig:
    """Load and validate board configuration from ``path``.

    Raises:
        BoardConfigError: the file is missing, is not valid YAML, is not a
            mapping, or fails validation.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        msg = f"cannot read board configuration at {path}: {exc}"
        raise BoardConfigError(msg) from exc

    try:
        raw: object = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        msg = f"board configuration at {path} is not valid YAML: {exc}"
        raise BoardConfigError(msg) from exc

    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        msg = (
            f"board configuration at {path} must be a mapping, got {type(raw).__name__}"
        )
        raise BoardConfigError(msg)

    try:
        return BoardsConfig.model_validate(raw)
    except ValidationError as exc:
        msg = f"invalid board configuration at {path}: {exc}"
        raise BoardConfigError(msg) from exc
