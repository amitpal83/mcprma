"""Tests for db.session.init_db()'s auto-add-missing-columns behavior.

Regression test for a real gap found during a live smoke test: an
already-existing sqlite file (created before new nullable columns were
added to Transaction) failed with "no such column" because
Base.metadata.create_all() only creates missing TABLES, never adds columns
to one that already exists. init_db() now diffs declared vs. actual
columns per table and issues ALTER TABLE ADD COLUMN for anything missing.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from db.models import Transaction
from db.session import init_db


@pytest.fixture
def old_schema_engine(tmp_path):
    """A throwaway sqlite file with an older transactions table: has the
    required columns (account_number/txn_date/merchant/txn_currency, the
    latter NOT NULL) but is missing several newer nullable columns --
    reproducing a database that predates those columns being added to
    Transaction. txn_currency itself can't be a "missing column" in this
    fixture: init_db()'s add-missing-columns logic only handles additive,
    nullable changes (see its own docstring) and can't backfill a NOT NULL
    column with no default onto a non-empty table -- that's real-migration
    territory, which this project deliberately doesn't have.
    """
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE accounts (account_number VARCHAR(34) PRIMARY KEY, display_name VARCHAR(120), created_at DATETIME)"))
        conn.execute(
            text(
                """
                CREATE TABLE transactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_number VARCHAR(34) NOT NULL,
                    txn_date DATE NOT NULL,
                    merchant VARCHAR(500) NOT NULL,
                    reference_no VARCHAR(50),
                    txn_amount_INR NUMERIC(18, 2),
                    created_at DATETIME,
                    card_id INTEGER,
                    txn_currency VARCHAR(3) NOT NULL
                )
                """
            )
        )
        conn.execute(text("INSERT INTO accounts (account_number) VALUES ('8552')"))
        conn.execute(
            text(
                """
                INSERT INTO transactions
                    (account_number, txn_date, merchant, reference_no,
                     txn_amount_INR, txn_currency)
                VALUES ('8552', '2026-09-01', 'PRE-EXISTING ROW', 'REF001', 100.00, 'INR')
                """
            )
        )
    return engine


def test_init_db_adds_missing_columns_without_touching_existing_rows(old_schema_engine):
    init_db(bind=old_schema_engine)

    inspector = inspect(old_schema_engine)
    columns = {col["name"] for col in inspector.get_columns("transactions")}
    assert {
        "card_type", "txn_amount", "exchange_rate",
        "forex_markup_amount_INR", "category", "parent_entity",
        "instrument_mode", "transaction_type",
    } <= columns

    Session = sessionmaker(bind=old_schema_engine)
    with Session() as session:
        row = session.query(Transaction).filter_by(reference_no="REF001").one()
        assert row.merchant == "PRE-EXISTING ROW"
        assert row.txn_amount_INR == Decimal("100.00")
        assert row.card_type is None  # new column, backfilled NULL on the old row


def test_init_db_is_idempotent_on_already_migrated_schema(old_schema_engine):
    init_db(bind=old_schema_engine)
    init_db(bind=old_schema_engine)  # must not raise "duplicate column" on the second call

    inspector = inspect(old_schema_engine)
    columns = [col["name"] for col in inspector.get_columns("transactions")]
    assert columns.count("card_type") == 1


def test_init_db_on_brand_new_db_creates_full_schema(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'new.db'}")
    init_db(bind=engine)

    inspector = inspect(engine)
    assert "card_products" in inspector.get_table_names()
    columns = {col["name"] for col in inspector.get_columns("transactions")}
    assert "card_id" in columns


def test_init_db_drops_obsolete_next_best_offer_columns_and_keeps_data(tmp_path):
    """A customer_360 table created before next-best-offer was removed (with its
    FOREIGN KEY to card_products) is rebuilt without those columns, keeping its
    rows and gaining the new relationship_tier / home_branch columns."""
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    init_db(bind=engine)  # current schema, so every referenced table exists
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE customer_360"))
        conn.execute(
            text(
                """
                CREATE TABLE customer_360 (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    customer_id INTEGER NOT NULL UNIQUE,
                    account_number VARCHAR(34) NOT NULL,
                    customer_name VARCHAR(120) NOT NULL,
                    onboarding_date DATE,
                    email_work VARCHAR(120),
                    email_personal VARCHAR(120),
                    addresses_json TEXT,
                    current_instruments_json TEXT,
                    latest_service_request_id INTEGER,
                    next_best_offer_product_external_id VARCHAR(50),
                    next_best_offer_product_id INTEGER,
                    next_best_offer_action_type VARCHAR(30),
                    next_best_offer_applicable_discounts VARCHAR(250),
                    next_best_offer_reason VARCHAR(250),
                    raw_json TEXT,
                    created_at DATETIME,
                    updated_at DATETIME,
                    FOREIGN KEY(next_best_offer_product_id) REFERENCES card_products (id)
                )
                """
            )
        )
        conn.execute(
            text(
                "INSERT INTO customer_360 (customer_id, account_number, customer_name, email_work, "
                "next_best_offer_reason, created_at, updated_at) VALUES "
                "(1, 'ACC101', 'VIPUL SINGH', 'work@email.com', 'HIGH FOREX', '2026-10-01', '2026-10-01')"
            )
        )

    init_db(bind=engine)

    columns = {col["name"] for col in inspect(engine).get_columns("customer_360")}
    assert not {c for c in columns if c.startswith("next_best_offer")}
    assert {"relationship_tier", "home_branch_name", "home_branch_address"} <= columns
    with engine.connect() as conn:
        row = conn.execute(text("SELECT customer_name, email_work, relationship_tier FROM customer_360")).one()
    assert tuple(row) == ("VIPUL SINGH", "work@email.com", None)
    assert "customer_360__old" not in inspect(engine).get_table_names()

    init_db(bind=engine)  # idempotent: nothing left to drop
