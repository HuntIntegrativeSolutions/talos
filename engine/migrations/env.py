import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def get_url() -> str:
    # TALOS_DB_DSN takes precedence (set by testcontainers and production env).
    # Fall back to the sqlalchemy.url from alembic.ini.
    dsn = os.environ.get("TALOS_DB_DSN")
    if dsn:
        # psycopg2 URL — ensure it has a driver suffix for SQLAlchemy.
        if dsn.startswith("postgresql://") and "+psycopg2" not in dsn:
            return dsn.replace("postgresql://", "postgresql+psycopg2://", 1)
        return dsn
    return config.get_main_option("sqlalchemy.url")


def run_migrations_online() -> None:
    url = get_url()
    connectable = create_engine(url, poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=None)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
