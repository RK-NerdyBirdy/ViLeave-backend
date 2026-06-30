"""
alembic/env.py
───────────────
Alembic migration environment.

Supports both:
  - `alembic upgrade head`        → online mode (connects to DB)
  - `alembic upgrade head --sql`  → offline mode (generates SQL script)

All models must be imported before `Base.metadata` is referenced so
Alembic can detect them for `--autogenerate`.
"""
import os
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool
from sqlalchemy import text

from alembic import context

# ── Load app config ───────────────────────────────────────────────────────────
# Must happen before importing models so Settings can read .env
from app.config import get_settings

settings_obj = get_settings()

# ── Import ALL models so Alembic sees them in Base.metadata ──────────────────
from app.database import Base
import app.models   # noqa: F401 — triggers __init__.py which imports all models

# Also import the blocklist model (not in app/models/__init__.py by convention)
from app.services.token_blocklist import BlockedToken  # noqa: F401

# ── Alembic config ────────────────────────────────────────────────────────────
config = context.config
config.set_main_option("sqlalchemy.url", settings_obj.sync_database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


# ── Migration runners ─────────────────────────────────────────────────────────

def run_migrations_offline() -> None:
    """
    Offline mode: emit SQL to stdout without connecting to the DB.
    Useful for generating migration scripts to review before running.

    Usage:
        alembic upgrade head --sql > migration.sql
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,          # Detect column type changes
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """
    Online mode: connect to the DB using the sync psycopg2 driver and run
    migrations. Alembic does not support asyncpg directly, hence the
    separate SYNC_DATABASE_URL in settings.
    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,   # Don't pool connections in migration context
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
