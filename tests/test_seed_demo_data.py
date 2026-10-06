"""Tests for etl.seed_demo_data (Step 9).

Each test builds its own throwaway SQLite file under pytest's tmp_path
fixture, so tests never touch the real data/rma.db -- and the seeder itself
only ever writes under its own fictional demo account number, never the
real personal account, so this is doubly safe even against a real db.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.repository.cards import get_card_forex_summary
from api.repository.merchants import resolve_merchant
from db.models import Base, Card, Transaction
from etl.seed_demo_data import DEFAULT_DEMO_ACCOUNT_NUMBER, WISDOM_PROPERTY_DESCRIPTOR, seed_demo_data

AS_OF = date(2026, 9, 30)  # fixed anchor: matches the docx's "12 Aug" falling inside the trailing 365 days


@pytest.fixture
def session_factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def test_seed_inserts_expected_rows(session_factory):
    result = seed_demo_data(session_factory=session_factory, as_of=AS_OF)

    assert result.merchants_inserted == 1
    assert result.aliases_inserted == 1
    assert result.card_products_inserted == 2
    assert result.cards_inserted == 1
    assert result.customers_inserted == 1
    assert result.transactions_inserted == 365  # imported from data/transactionlist.py (no scripted forex rows anymore)
    assert result.service_requests_inserted == 1
    assert result.customer_360_inserted == 1
    assert result.errors == []


def test_wisdom_property_alias_still_resolves_though_no_transaction_uses_it(session_factory):
    """The merchant alias is seeded unconditionally (for fuzzy-matching), but
    the dedicated demo forex batch that used to create a transaction under
    this exact descriptor was deliberately dropped -- so the alias resolves,
    yet no transaction references it anymore.
    """
    seed_demo_data(session_factory=session_factory, as_of=AS_OF)

    with session_factory() as session:
        merchant = resolve_merchant(session, WISDOM_PROPERTY_DESCRIPTOR)
        assert merchant.brand_name == "DoubleTree by Hilton Amsterdam Centraal Station"

        txn = session.query(Transaction).filter_by(merchant=WISDOM_PROPERTY_DESCRIPTOR).first()
        assert txn is None


def test_reseed_is_idempotent(session_factory):
    seed_demo_data(session_factory=session_factory, as_of=AS_OF)
    second = seed_demo_data(session_factory=session_factory, as_of=AS_OF)

    assert second.merchants_inserted == 0
    assert second.aliases_inserted == 0
    assert second.card_products_inserted == 0
    assert second.cards_inserted == 0
    assert second.customers_inserted == 0
    assert second.transactions_inserted == 0
    assert second.service_requests_inserted == 0
    assert second.customer_360_inserted == 0
    assert second.skipped > 0


def test_forex_summary_reflects_merged_transaction_history(session_factory):
    """Trailing-365-day forex summary spans the 365-day data/transactionlist.py
    import (no scripted forex rows anymore), so it reflects Vipul Singh's
    full imported spending history as-is.
    """
    seed_demo_data(session_factory=session_factory, as_of=AS_OF)

    with session_factory() as session:
        card = session.query(Card).filter_by(account_number=DEFAULT_DEMO_ACCOUNT_NUMBER).first()
        summary = get_card_forex_summary(session, card.id, as_of_date=AS_OF)

        assert summary.total_forex_spend_inr == Decimal("511801.52")
        assert summary.total_markup_amount == Decimal("17913.02")
        assert summary.transaction_count == 253
