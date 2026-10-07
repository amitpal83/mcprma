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
    Customer360,
    Dispute,
    Merchant,
    MerchantAlias,
    ServiceRequest,
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
    "service_requests",
    "customer_360",
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
        txn_date=date(2026, 8, 12),
        merchant="WISDOM PROPERTY NL II",
        txn_amount_INR=353,
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
    session.flush()

    service_request = ServiceRequest(
        customer_id=customer.id,
        service_request_id="SR1156788-20261001",
        service_request_type="account_statement",
        service_request_date=date(2026, 10, 1),
        service_request_status="under progress",
    )
    session.add(service_request)
    session.flush()

    customer_360 = Customer360(
        customer_id=customer.id,
        account_number=account.account_number,
        customer_name="VIPUL SINGH",
        latest_service_request_id=service_request.id,
        relationship_tier=3,
    )
    session.add(customer_360)
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
        "service_request": service_request,
        "customer_360": customer_360,
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
        assert session.get(ServiceRequest, ids["service_request"].id).service_request_id == "SR1156788-20261001"
        assert session.get(Customer360, ids["customer_360"].id).customer_name == "VIPUL SINGH"


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


def test_duplicate_service_request_id_rejected(session_factory):
    factory, _ = session_factory
    with factory() as session:
        account = Account(account_number="8552")
        customer = Customer(account_number="8552", full_name="Vipul Singh", registered_email="a@example.com")
        session.add_all([account, customer])
        session.flush()

        session.add(
            ServiceRequest(
                customer_id=customer.id,
                service_request_id="SR1156788-20261001",
                service_request_type="account_statement",
                service_request_date=date(2026, 10, 1),
                service_request_status="under progress",
            )
        )
        session.commit()

        session.add(
            ServiceRequest(
                customer_id=customer.id,
                service_request_id="SR1156788-20261001",
                service_request_type="card_replacement",
                service_request_date=date(2026, 10, 2),
                service_request_status="submitted",
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


def test_duplicate_customer_360_per_customer_rejected(session_factory):
    factory, _ = session_factory
    with factory() as session:
        account = Account(account_number="8552")
        customer = Customer(account_number="8552", full_name="Vipul Singh", registered_email="a@example.com")
        session.add_all([account, customer])
        session.flush()

        session.add(Customer360(customer_id=customer.id, account_number="8552", customer_name="VIPUL SINGH"))
        session.commit()

        session.add(Customer360(customer_id=customer.id, account_number="8552", customer_name="VIPUL SINGH"))
        with pytest.raises(IntegrityError):
            session.commit()
