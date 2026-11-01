from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from visentra_api import models  # noqa: F401  (registers tables)
from visentra_api.config import settings, resolve_database_url
from visentra_api.db import Base

config = context.config
if not config.get_main_option("sqlalchemy.url") or config.get_main_option("sqlalchemy.url").startswith("driver://"):
    config.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))
else:
    config.set_main_option("sqlalchemy.url", resolve_database_url(config.get_main_option("sqlalchemy.url")).replace("%", "%%"))
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    try:
        fileConfig(config.config_file_name, disable_existing_loggers=False)
    except Exception:
        pass
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(url=config.get_main_option("sqlalchemy.url"), target_metadata=target_metadata, literal_binds=True, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(config.get_section(config.config_ini_section, {}), prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
