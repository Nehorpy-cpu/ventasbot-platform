"""Persistencia SQL para el monolito modular.

SQLite se usa en desarrollo y pruebas. DATABASE_URL puede apuntar a PostgreSQL
sin cambiar los repositorios ni los modelos de dominio.
"""

from __future__ import annotations

import os
from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


def _database_url() -> str:
    return os.getenv("DATABASE_URL", "sqlite:///./ventasbot.db")


def build_engine(url: str | None = None):
    selected = url or _database_url()
    kwargs = {"check_same_thread": False} if selected.startswith("sqlite") else {}
    return create_engine(selected, connect_args=kwargs, pool_pre_ping=True)


engine = build_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_schema() -> None:
    from . import models  # noqa: F401 - registra las tablas

    Base.metadata.create_all(bind=engine)
