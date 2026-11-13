"""Runtime settings, loaded from the environment.

Values are read from ``JOB_RADAR_``-prefixed environment variables, falling
back to a local ``.env`` file. Defaults match ``docker-compose.yml`` so a
fresh checkout runs without configuration; deployments must override them.
"""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_DATABASE_URL = "postgresql+psycopg://jobradar:jobradar@localhost:5432/jobradar"


class DatabaseSettings(BaseSettings):
    """What a command that only reads the database needs: where it is.

    ``job-radar jobs`` loads this alone, so a missing or invalid ingestion
    setting (board file, HTTP timeout) cannot stop a read. ``extra="ignore"``
    keeps the other ``JOB_RADAR_*`` keys in ``.env`` from being rejected.
    """

    model_config = SettingsConfigDict(
        env_prefix="JOB_RADAR_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    database_url: str = Field(
        default=DEFAULT_DATABASE_URL,
        description="SQLAlchemy URL; must use the psycopg 3 driver.",
    )


class Settings(DatabaseSettings):
    """Process-wide configuration for ingestion: the database, the board file
    and the HTTP client. Same sources and precedence as ``DatabaseSettings``.
    """

    boards_config_path: Path = Field(
        default=Path("config/boards.yml"),
        description="Version-controlled board configuration (ADR-0012).",
    )
    http_timeout_seconds: float = Field(
        default=10.0,
        gt=0,
        description="Bounded HTTP timeout for the collector's client (ADR-0009).",
    )
