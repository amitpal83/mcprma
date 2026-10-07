"""Database engine/session management for the RMA SQLite database.

Application code should import SessionLocal (or call init_db() once at
startup) rather than constructing its own engine, so every module talks to
the same database file.
"""
from __future__ import annotations

import logging

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.schema import CreateColumn, CreateTable

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
    exists this is a no-op. Additive changes are automatic; removals only for
    columns listed in OBSOLETE_COLUMNS (see _drop_obsolete_columns). A NOT NULL
    column with no default or a rename still needs a real migration tool.

    bind defaults to the module-level engine; tests inject a throwaway one.
    """
    target_engine = bind or engine
    Base.metadata.create_all(bind=target_engine)
    _add_missing_columns(target_engine)
    _drop_obsolete_columns(target_engine)


# Columns removed from a model that must also disappear from databases created
# before the removal. An explicit list (rather than "anything the model doesn't
# declare") so init_db never drops a column it wasn't told about.
OBSOLETE_COLUMNS: dict[str, frozenset[str]] = {
    "customer_360": frozenset(
        {
            "next_best_offer_product_external_id",
            "next_best_offer_product_id",
            "next_best_offer_action_type",
            "next_best_offer_applicable_discounts",
            "next_best_offer_reason",
        }
    ),
}


def _drop_obsolete_columns(bind) -> None:
    """Remove columns listed in OBSOLETE_COLUMNS from tables that still have them.

    ALTER TABLE DROP COLUMN can't be used here: SQLite refuses to drop a column
    that is part of a FOREIGN KEY (next_best_offer_product_id is). So the table
    is rebuilt instead -- renamed aside, recreated from the model, data copied
    across for every column both versions share, old table dropped.

    The rebuild runs inside one explicit BEGIN IMMEDIATE transaction on a raw
    sqlite3 connection (pysqlite's implicit transactions don't cover DDL), so
    it is all-or-nothing, and the API and MCP services -- which both call
    init_db on startup -- can't interleave: the second one waits for the write
    lock, re-reads the columns, and finds nothing left to do. Safe to call
    repeatedly (no-op once the columns are gone).

    Only handles tables with no named indexes (customer_360 has none); extend
    it before listing a table that has any.
    """
    for table in Base.metadata.sorted_tables:
        stale_names = OBSOLETE_COLUMNS.get(table.name)
        if not stale_names:
            continue
        if not {col["name"] for col in inspect(bind).get_columns(table.name)} & stale_names:
            continue

        create_ddl = str(CreateTable(table).compile(dialect=bind.dialect))
        raw = bind.raw_connection()
        try:
            dbapi = getattr(raw, "driver_connection", None) or raw.connection
            previous_isolation = dbapi.isolation_level
            dbapi.isolation_level = None  # we issue BEGIN/COMMIT ourselves
            cursor = dbapi.cursor()
            cursor.execute("BEGIN IMMEDIATE")
            try:
                existing = {row[1] for row in cursor.execute(f'PRAGMA table_info("{table.name}")')}
                dropped = existing & stale_names
                if dropped:
                    kept = ", ".join(f'"{col.name}"' for col in table.columns if col.name in existing)
                    cursor.execute(f'ALTER TABLE "{table.name}" RENAME TO "{table.name}__old"')
                    cursor.execute(create_ddl)
                    cursor.execute(f'INSERT INTO "{table.name}" ({kept}) SELECT {kept} FROM "{table.name}__old"')
                    cursor.execute(f'DROP TABLE "{table.name}__old"')
                cursor.execute("COMMIT")
            except Exception:
                cursor.execute("ROLLBACK")
                raise
            finally:
                dbapi.isolation_level = previous_isolation
        finally:
            raw.close()

        if dropped:
            logger.info("init_db: dropped obsolete columns %s from %s", sorted(dropped), table.name)


def _add_missing_columns(bind) -> None:
    inspector = inspect(bind)
    with bind.begin() as conn:
        for table in Base.metadata.sorted_tables:
            existing_columns = {col["name"] for col in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing_columns:
                    continue
                ddl = CreateColumn(column).compile(dialect=conn.dialect)
                try:
                    conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN {ddl}'))
                except OperationalError as exc:
                    # The API and MCP services both run init_db on startup; the
                    # other one may have added this column a moment ago.
                    if "duplicate column" not in str(exc).lower():
                        raise
                    continue
                logger.info("init_db: added missing column %s.%s", table.name, column.name)
