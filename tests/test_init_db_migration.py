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
    """A throwaway sqlite file with the OLD (pre-Step-1) transactions table:
    no card_id/merchant_id/txn_currency/... columns -- reproducing exactly
    what the real data/rma.db looked like before this session's schema work.
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
                    value_date DATE NOT NULL,
                    narration VARCHAR(500) NOT NULL,
                    reference_no VARCHAR(50),
                    withdrawal_amount NUMERIC(18, 2),
                    deposit_amount NUMERIC(18, 2),
                    closing_balance NUMERIC(18, 2) NOT NULL,
                    created_at DATETIME
                )
                """
            )
        )
        conn.execute(text("INSERT INTO accounts (account_number) VALUES ('8552')"))
        conn.execute(
            text(
                """
                INSERT INTO transactions
                    (account_number, txn_date, value_date, narration, reference_no,
                     withdrawal_amount, deposit_amount, closing_balance)
                VALUES ('8552', '2026-09-01', '2026-09-01', 'PRE-EXISTING ROW', 'REF001', 100.00, NULL, 900.00)
                """
            )
        )
    return engine


def test_init_db_adds_missing_columns_without_touching_existing_rows(old_schema_engine):
    init_db(bind=old_schema_engine)

    inspector = inspect(old_schema_engine)
    columns = {col["name"] for col in inspector.get_columns("transactions")}
    assert {
        "card_id", "merchant_id", "txn_currency", "txn_amount", "exchange_rate",
        "forex_markup_pct", "forex_markup_amount", "gst_on_markup", "mcc", "category",
    } <= columns

    Session = sessionmaker(bind=old_schema_engine)
    with Session() as session:
        row = session.query(Transaction).filter_by(reference_no="REF001").one()
        assert row.narration == "PRE-EXISTING ROW"
        assert row.withdrawal_amount == Decimal("100.00")
        assert row.card_id is None  # new column, backfilled NULL on the old row


def test_init_db_is_idempotent_on_already_migrated_schema(old_schema_engine):
    init_db(bind=old_schema_engine)
    init_db(bind=old_schema_engine)  # must not raise "duplicate column" on the second call

    inspector = inspect(old_schema_engine)
    columns = [col["name"] for col in inspector.get_columns("transactions")]
    assert columns.count("card_id") == 1


def test_init_db_on_brand_new_db_creates_full_schema(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'new.db'}")
    init_db(bind=engine)

    inspector = inspect(engine)
    assert "card_products" in inspector.get_table_names()
    columns = {col["name"] for col in inspector.get_columns("transactions")}
    assert "card_id" in columns
