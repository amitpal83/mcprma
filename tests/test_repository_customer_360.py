"""Tests for api.repository.customer_360."""
from __future__ import annotations

import json
from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.repository.customer_360 import Customer360NotFoundError, get_customer_360
from db.models import Account, Base, CardProduct, Customer, Customer360

ACCOUNT_NUMBER = "8552"


@pytest.fixture
def session_factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def seeded_session_factory(session_factory):
    with session_factory() as session:
        session.add(Account(account_number=ACCOUNT_NUMBER))
        customer = Customer(
            account_number=ACCOUNT_NUMBER,
            full_name="Vipul Singh",
            registered_email="work@email.com",
            relationship_tier="PRIORITY",
        )
        credit_product = CardProduct(
            name="Global Elite zero forex markup credit card",
            network="Visa",
            card_type="credit",
            forex_markup_pct=0,
            external_product_id="prod-2",
        )
        session.add_all([customer, credit_product])
        session.flush()

        session.add(
            Customer360(
                customer_id=customer.id,
                account_number=ACCOUNT_NUMBER,
                customer_name="VIPUL SINGH",
                onboarding_date=date(2023, 1, 15),
                email_work="work@email.com",
                email_personal="personal@email.com",
                addresses_json=json.dumps([{"address_type": "home", "address": "Noida", "preferred_flag": False}]),
                current_instruments_json=json.dumps([{"instrument_type": "debit_card"}]),
                next_best_offer_product_external_id="prod-2",
                next_best_offer_product_id=credit_product.id,
                next_best_offer_action_type="cross_sell",
                next_best_offer_applicable_discounts="25% on joining fee",
                next_best_offer_reason="HIGH FOREX Spending",
                raw_json=json.dumps({"customer_name": "VIPUL SINGH"}),
            )
        )
        session.commit()

    return session_factory


def test_get_customer_360_returns_snapshot(seeded_session_factory):
    with seeded_session_factory() as session:
        snapshot = get_customer_360(session, ACCOUNT_NUMBER)
        assert snapshot.customer_name == "VIPUL SINGH"
        assert snapshot.next_best_offer_product_external_id == "prod-2"
        assert snapshot.next_best_offer_action_type == "cross_sell"
        assert snapshot.next_best_offer_reason == "HIGH FOREX Spending"
        assert json.loads(snapshot.addresses_json)[0]["address"] == "Noida"


def test_get_customer_360_unknown_account_raises(seeded_session_factory):
    with seeded_session_factory() as session:
        with pytest.raises(Customer360NotFoundError):
            get_customer_360(session, "unknown")
