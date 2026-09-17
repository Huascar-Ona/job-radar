"""Runtime settings, loaded from the environment.

Values are read from ``JOB_RADAR_``-prefixed environment variables, falling
back to a local ``.env`` file. Defaults match ``docker-compose.yml`` so a
fresh checkout runs without configuration; deployments must override them.
"""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_DATABASE_URL = "postgresql+psycopg://jobradar:jobradar@localhost:5432/jobradar"


class Settings(BaseSettings):
    """Process-wide configuration."""

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
    boards_config_path: Path = Field(
        default=Path("config/boards.yml"),
        description="Version-controlled board configuration (ADR-0012).",
    )
    http_timeout_seconds: float = Field(
        default=10.0,
        gt=0,
        description="Bounded HTTP timeout (ADR-0009). Unused until a collector exists.",
    )
