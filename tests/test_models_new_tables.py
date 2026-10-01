"""Tests for the new card/merchant/customer/dispute/application tables.

Each test builds its own throwaway SQLite file under pytest's tmp_path
fixture, so tests never touch the real data/rma.db. This file only checks
the schema itself (tables exist, FKs/unique constraints behave) -- repository
logic for these tables is covered separately per domain (see Step 2 onward).
"""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from db.models import (
    Account,
    Base,
    Card,
    CardApplication,
    CardProduct,
    Customer,
    Dispute,
    Merchant,
    MerchantAlias,
    Transaction,
)

NEW_TABLES = {
    "card_products",
    "cards",
    "merchants",
    "merchant_aliases",
    "customers",
    "disputes",
    "card_applications",
}


@pytest.fixture
def session_factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, expire_on_commit=False), engine


def test_all_new_tables_created(session_factory):
    _, engine = session_factory
    table_names = set(inspect(engine).get_table_names())
    assert NEW_TABLES <= table_names


def _seed_core_rows(session):
    """Insert one valid, linked row per new table. Returns key ids for reuse."""
    account = Account(account_number="8552")
    session.add(account)
    session.flush()

    card_product = CardProduct(
        name="Global Elite Zero Forex Card",
        network="Visa",
        card_type="credit",
        forex_markup_pct=0,
        joining_fee=15000,
        annual_fee=15000,
    )
    session.add(card_product)
    session.flush()

    card = Card(
        account_number=account.account_number,
        card_product_id=card_product.id,
        last4="4821",
        network="Visa",
        card_type="debit",
    )
    session.add(card)
    session.flush()

    merchant = Merchant(brand_name="DoubleTree by Hilton", city="Amsterdam", country="NL")
    session.add(merchant)
    session.flush()

    alias = MerchantAlias(merchant_id=merchant.id, raw_pattern="WISDOM PROPERTY NL II")
    session.add(alias)
    session.flush()

    transaction = Transaction(
        account_number=account.account_number,
        card_id=card.id,
        merchant_id=merchant.id,
        txn_date=date(2026, 8, 12),
        value_date=date(2026, 8, 12),
        narration="WISDOM PROPERTY NL II",
        withdrawal_amount=353,
        closing_balance=100000,
        txn_currency="EUR",
        txn_amount=353,
    )
    session.add(transaction)
    session.flush()

    customer = Customer(
        account_number=account.account_number,
        full_name="Mr. Mehta",
        registered_email="mehta@example.com",
    )
    session.add(customer)
    session.flush()

    dispute = Dispute(transaction_id=transaction.id, reason="Unrecognized charge")
    session.add(dispute)
    session.flush()

    application = CardApplication(customer_id=customer.id, card_product_id=card_product.id)
    session.add(application)
    session.commit()

    return {
        "account": account,
        "card_product": card_product,
        "card": card,
        "merchant": merchant,
        "alias": alias,
        "transaction": transaction,
        "customer": customer,
        "dispute": dispute,
        "application": application,
    }


def test_insert_one_row_per_table_round_trips(session_factory):
    factory, _ = session_factory
    with factory() as session:
        ids = _seed_core_rows(session)

    with factory() as session:
        assert session.get(CardProduct, ids["card_product"].id) is not None
        assert session.get(Card, ids["card"].id).last4 == "4821"
        assert session.get(Merchant, ids["merchant"].id).brand_name == "DoubleTree by Hilton"
        assert session.get(MerchantAlias, ids["alias"].id).raw_pattern == "WISDOM PROPERTY NL II"
        assert session.get(Customer, ids["customer"].id).full_name == "Mr. Mehta"
        assert session.get(Dispute, ids["dispute"].id).status == "OPEN"
        assert session.get(CardApplication, ids["application"].id).status == "SUBMITTED"


def test_duplicate_card_product_name_rejected(session_factory):
    factory, _ = session_factory
    with factory() as session:
        session.add(CardProduct(name="Dup Card", network="Visa", card_type="credit", forex_markup_pct=0))
        session.commit()

        session.add(CardProduct(name="Dup Card", network="Visa", card_type="credit", forex_markup_pct=0))
        with pytest.raises(IntegrityError):
            session.commit()


def test_duplicate_card_identity_rejected(session_factory):
    factory, _ = session_factory
    with factory() as session:
        account = Account(account_number="8552")
        product = CardProduct(name="Debit Card", network="Visa", card_type="debit", forex_markup_pct=3.5)
        session.add_all([account, product])
        session.flush()

        session.add(Card(account_number="8552", card_product_id=product.id, last4="4821", network="Visa", card_type="debit"))
        session.commit()

        session.add(Card(account_number="8552", card_product_id=product.id, last4="4821", network="Visa", card_type="debit"))
        with pytest.raises(IntegrityError):
            session.commit()


def test_duplicate_merchant_identity_rejected(session_factory):
    factory, _ = session_factory
    with factory() as session:
        session.add(Merchant(brand_name="DoubleTree by Hilton", city="Amsterdam"))
        session.commit()

        session.add(Merchant(brand_name="DoubleTree by Hilton", city="Amsterdam"))
        with pytest.raises(IntegrityError):
            session.commit()


def test_duplicate_merchant_alias_pattern_rejected(session_factory):
    factory, _ = session_factory
    with factory() as session:
        merchant = Merchant(brand_name="DoubleTree by Hilton", city="Amsterdam")
        session.add(merchant)
        session.flush()

        session.add(MerchantAlias(merchant_id=merchant.id, raw_pattern="WISDOM PROPERTY NL II"))
        session.commit()

        session.add(MerchantAlias(merchant_id=merchant.id, raw_pattern="WISDOM PROPERTY NL II"))
        with pytest.raises(IntegrityError):
            session.commit()


def test_duplicate_customer_account_rejected(session_factory):
    factory, _ = session_factory
    with factory() as session:
        account = Account(account_number="8552")
        session.add(account)
        session.flush()

        session.add(Customer(account_number="8552", full_name="Mr. Mehta", registered_email="a@example.com"))
        session.commit()

        session.add(Customer(account_number="8552", full_name="Mr. Mehta Duplicate", registered_email="b@example.com"))
        with pytest.raises(IntegrityError):
            session.commit()
