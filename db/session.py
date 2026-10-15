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
    
    """
    target_engine = bind or engine
    Base.metadata.create_all(bind=target_engine)
    _add_missing_columns(target_engine)
    _drop_obsolete_columns(target_engine)


# Columns removed from a model that must also disappear from databases created
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
