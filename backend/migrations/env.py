"""Alembic environment: migrations run as blastproof_owner, and nothing else (BE-14).

The URL is a plain postgresql:// URL, so SQLAlchemy 2.1 uses psycopg 3, its default driver.
Tests pass their own URL in `config.attributes["url"]`; otherwise it comes from
BLASTPROOF_MIGRATOR_URL. The URL is never logged.
"""

import logging

from alembic import context
from sqlalchemy import create_engine, pool, text

from app.config import Settings
from app.db import models  # noqa: F401  (registers every table on Base.metadata)
from app.db.base import Base
from app.db.urls import check_migrator_role, migrator_url
from app.logging import configure_logging

config = context.config
explicit_url = config.attributes.get("url")
# With an explicit URL (tests), backend/.env is never read.
settings = Settings() if explicit_url is None else Settings(_env_file=None)

# Only when run from the command line (tests keep their own logging): JSON lines, like the app,
# so migrations run by Fly.io's release_command match the rest of the logs.
if explicit_url is None:
    configure_logging(settings.log_level)
    # SQLAlchemy logs every statement once its logger allows INFO; keep SQL out of the logs.
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """`alembic upgrade head --sql`: print the SQL instead of running it."""
    context.configure(
        url=migrator_url(explicit_url, settings),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(migrator_url(explicit_url, settings), poolclass=pool.NullPool)
    try:
        with engine.connect() as connection:
            # One transaction for the whole run: a failure leaves nothing half-built. The role
            # check runs inside it: a query before begin_transaction() would start a transaction
            # Alembic then joins and never commits.
            context.configure(connection=connection, target_metadata=target_metadata)
            with context.begin_transaction():
                check_migrator_role(connection.execute(text("SELECT current_user")).scalar_one())
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
