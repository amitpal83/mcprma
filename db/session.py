"""Database engine/session management for the RMA SQLite database.

Application code should import SessionLocal (or call init_db() once at
startup) rather than constructing its own engine, so every module talks to
the same database file.
"""
from __future__ import annotations

import logging

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.schema import CreateColumn

from config.settings import DATABASE_URL
from db.models import Base

logger = logging.getLogger(__name__)

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db(bind=None) -> None:
    """Create all tables if they do not already exist, then add any columns
    a model declares that an already-existing table doesn't have yet.

    There is no migration tool in this project (no Alembic) --
    Base.metadata.create_all() alone only creates missing tables, it never
    alters one that already exists. _add_missing_columns() fills that gap
    for the common case (a new nullable column added to an existing model)
    by diffing declared vs. actual columns and issuing ALTER TABLE ADD
    COLUMN for anything missing. Safe to call repeatedly -- once a column
    exists this is a no-op. Only handles additive changes: a NOT NULL
    column with no default, a rename, or a dropped column still needs a
    real migration tool.

    bind defaults to the module-level engine; tests inject a throwaway one.
    """
    target_engine = bind or engine
    Base.metadata.create_all(bind=target_engine)
    _add_missing_columns(target_engine)


def _add_missing_columns(bind) -> None:
    inspector = inspect(bind)
    with bind.begin() as conn:
        for table in Base.metadata.sorted_tables:
            existing_columns = {col["name"] for col in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing_columns:
                    continue
                ddl = CreateColumn(column).compile(dialect=conn.dialect)
                conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN {ddl}'))
                logger.info("init_db: added missing column %s.%s", table.name, column.name)
