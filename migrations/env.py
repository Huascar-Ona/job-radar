"""Alembic environment.

The database URL comes from Settings rather than alembic.ini, so credentials
stay out of version control.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from job_radar.persistence import models  # noqa: F401  (registers tables)
from job_radar.persistence.base import Base
from job_radar.settings import Settings

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Tests bind an explicit URL; otherwise fall back to ambient settings.
if not config.get_main_option("sqlalchemy.url", None):
    config.set_main_option("sqlalchemy.url", Settings().database_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Emit SQL to stdout without a DBAPI connection."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live connection."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
