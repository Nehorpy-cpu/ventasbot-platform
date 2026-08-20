"""Configuración de Alembic: la URL y los modelos salen de la app, no del .ini."""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from dotenv import load_dotenv

from app.database import Base, _database_url
from app import models  # noqa: F401 - importa para que Base conozca las tablas

load_dotenv()

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", _database_url().replace("%", "%%"))
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # SQLite no sabe hacer ALTER de verdad: sin esto, cualquier cambio de
        # columna en desarrollo falla.
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    from app.database import build_engine

    connectable = build_engine()
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
