"""Tests for card-transaction search, forex summary, and category breakdown
(api.repository.cards, Step 4)."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.repository.cards import (
    CardNotFoundError,
    InvalidAmountRangeError,
    get_card_category_breakdown,
    get_card_forex_summary,
    search_card_transactions,
)
from api.repository.transactions import InvalidDateRangeError
from db.models import Account, Base, Card, CardProduct, Merchant, MerchantAlias, Transaction

ACCOUNT_NUMBER = "8552"
AS_OF = date(2026, 9, 30)  # fixed anchor so the 365-day window is deterministic


@pytest.fixture
def session_factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def seeded_session_factory(session_factory):
    with session_factory() as session:
        session.add(Account(account_number=ACCOUNT_NUMBER))

        product = CardProduct(name="HDFC Debit Card", network="Visa", card_type="debit", forex_markup_pct=3.5)
        session.add(product)
        session.flush()

        card = Card(
            account_number=ACCOUNT_NUMBER,
            card_product_id=product.id,
            last4="4821",
            network="Visa",
            card_type="debit",
        )
        session.add(card)
        session.flush()

        merchant = Merchant(
            brand_name="DoubleTree by Hilton Amsterdam Centraal Station",
            associated_property="DoubleTree by Hilton Amsterdam Centraal Station",
            category="Travel",
            city="Amsterdam",
            country="NL",
        )
        session.add(merchant)
        session.flush()
        session.add(MerchantAlias(merchant_id=merchant.id, raw_pattern="WISDOM PROPERTY NL II"))

        session.add_all(
            [
                # In-window forex txn -- the disputed-then-confirmed charge.
                Transaction(
                    account_number=ACCOUNT_NUMBER,
                    card_id=card.id,
                    txn_date=date(2026, 8, 12),
                    merchant="WISDOM PROPERTY NL II",
                    reference_no="SEED-FX-001",
                    txn_amount_INR=Decimal("32000.00"),
                    txn_currency="EUR",
                    txn_amount=Decimal("353.00"),
                    forex_markup_amount_INR=Decimal("1120.00"),
                    category="Travel",
                ),
                # In-window forex txn, different merchant/category.
                Transaction(
                    account_number=ACCOUNT_NUMBER,
                    card_id=card.id,
                    txn_date=date(2026, 5, 1),
                    merchant="STARBUCKS COFFEE SG",
                    reference_no="SEED-FX-002",
                    txn_amount_INR=Decimal("8300.00"),
                    txn_currency="USD",
                    txn_amount=Decimal("100.00"),
                    forex_markup_amount_INR=Decimal("290.50"),
                    category="Dining",
                ),
                # Forex txn, but older than 365 days before AS_OF -- excluded from summary.
                Transaction(
                    account_number=ACCOUNT_NUMBER,
                    card_id=card.id,
                    txn_date=date(2025, 1, 1),
                    merchant="OLD PARIS STORE",
                    reference_no="SEED-FX-003",
                    txn_amount_INR=Decimal("18000.00"),
                    txn_currency="EUR",
                    txn_amount=Decimal("200.00"),
                    forex_markup_amount_INR=Decimal("630.00"),
                    category="Shopping",
                ),
                # Non-forex domestic spend -- excluded from forex summary,
                # included in category breakdown.
                Transaction(
                    txn_currency="INR",
                    account_number=ACCOUNT_NUMBER,
                    card_id=card.id,
                    txn_date=date(2026, 7, 1),
                    merchant="BIG BAZAAR MUMBAI",
                    reference_no="SEED-FX-004",
                    txn_amount_INR=Decimal("500.00"),
                    category="Groceries",
                ),
            ]
        )
        session.commit()

    return session_factory, card.id


def test_search_by_amount_range_and_merchant_text_finds_disputed_txn(seeded_session_factory):
    factory, card_id = seeded_session_factory
    with factory() as session:
        results = search_card_transactions(
            session,
            card_id,
            from_date=date(2026, 8, 1),
            to_date=date(2026, 8, 31),
            amount_min=Decimal("340"),
            amount_max=Decimal("360"),
            merchant_text="Wisdom Property",
        )
        assert len(results) == 1
        assert results[0].reference_no == "SEED-FX-001"


def test_search_merchant_text_falls_back_to_merchant_substring(seeded_session_factory):
    factory, card_id = seeded_session_factory
    with factory() as session:
        results = search_card_transactions(
            session, card_id, from_date=date(2026, 1, 1), to_date=date(2026, 12, 31), merchant_text="starbucks"
        )
        assert len(results) == 1
        assert results[0].reference_no == "SEED-FX-002"


def test_search_unknown_card_raises(seeded_session_factory):
    factory, _ = seeded_session_factory
    with factory() as session:
        with pytest.raises(CardNotFoundError):
            search_card_transactions(session, 99999, from_date=date(2026, 1, 1), to_date=date(2026, 12, 31))


def test_search_invalid_date_range_raises(seeded_session_factory):
    factory, card_id = seeded_session_factory
    with factory() as session:
        with pytest.raises(InvalidDateRangeError):
            search_card_transactions(session, card_id, from_date=date(2026, 12, 31), to_date=date(2026, 1, 1))


def test_search_invalid_amount_range_raises(seeded_session_factory):
    """amount_min > amount_max previously matched nothing silently instead of
    raising -- a reversed range is a caller mistake, not a legitimate 'no
    transactions in this range' result."""
    factory, card_id = seeded_session_factory
    with factory() as session:
        with pytest.raises(InvalidAmountRangeError):
            search_card_transactions(
                session,
                card_id,
                from_date=date(2026, 1, 1),
                to_date=date(2026, 12, 31),
                amount_min=Decimal("39000"),
                amount_max=Decimal("21000"),
            )


def test_forex_summary_totals_trailing_365_days(seeded_session_factory):
    factory, card_id = seeded_session_factory
    with factory() as session:
        summary = get_card_forex_summary(session, card_id, as_of_date=AS_OF)

        assert summary.transaction_count == 2
        assert summary.total_forex_spend_inr == Decimal("40300.00")
        assert summary.total_markup_amount == Decimal("1410.50")


def test_forex_summary_unknown_card_raises(seeded_session_factory):
    factory, _ = seeded_session_factory
    with factory() as session:
        with pytest.raises(CardNotFoundError):
            get_card_forex_summary(session, 99999, as_of_date=AS_OF)


def test_category_breakdown_groups_all_categories_in_range(seeded_session_factory):
    factory, card_id = seeded_session_factory
    with factory() as session:
        breakdown = get_card_category_breakdown(
            session, card_id, from_date=date(2025, 1, 1), to_date=date(2026, 9, 30)
        )
        by_category = {row.category: row for row in breakdown}

        assert by_category["Travel"].total_amount == Decimal("32000.00")
        assert by_category["Dining"].total_amount == Decimal("8300.00")
        assert by_category["Shopping"].total_amount == Decimal("18000.00")
        assert by_category["Groceries"].total_amount == Decimal("500.00")
        assert by_category["Groceries"].transaction_count == 1
