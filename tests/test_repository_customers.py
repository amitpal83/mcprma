"""Tests for api.repository.customers (Step 5)."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.repository.customers import (
    CustomerNotFoundError,
    InvalidDeliveryAddressTypeError,
    get_customer_by_account,
    update_customer_delivery_preference,
)
from db.models import Account, Base, Customer

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
        session.add(
            Customer(
                account_number=ACCOUNT_NUMBER,
                full_name="Mr. Mehta",
                registered_email="rammehta@gmail.com",
                relationship_tier="PRIORITY",
            )
        )
        session.commit()
    return session_factory


def test_get_customer_by_account_returns_customer(seeded_session_factory):
    with seeded_session_factory() as session:
        customer = get_customer_by_account(session, ACCOUNT_NUMBER)
        assert customer.full_name == "Mr. Mehta"


def test_get_customer_by_unknown_account_raises(seeded_session_factory):
    with seeded_session_factory() as session:
        with pytest.raises(CustomerNotFoundError):
            get_customer_by_account(session, "unknown")


def test_update_delivery_preference_persists(seeded_session_factory):
    with seeded_session_factory() as session:
        customer_id = get_customer_by_account(session, ACCOUNT_NUMBER).id

    with seeded_session_factory() as session:
        updated = update_customer_delivery_preference(session, customer_id, "OFFICE")
        assert updated.preferred_delivery_address_type == "OFFICE"

    with seeded_session_factory() as session:
        reloaded = get_customer_by_account(session, ACCOUNT_NUMBER)
        assert reloaded.preferred_delivery_address_type == "OFFICE"


def test_update_delivery_preference_invalid_value_raises(seeded_session_factory):
    with seeded_session_factory() as session:
        customer_id = get_customer_by_account(session, ACCOUNT_NUMBER).id

    with seeded_session_factory() as session:
        with pytest.raises(InvalidDeliveryAddressTypeError):
            update_customer_delivery_preference(session, customer_id, "WAREHOUSE")


def test_update_delivery_preference_unknown_customer_raises(seeded_session_factory):
    with seeded_session_factory() as session:
        with pytest.raises(CustomerNotFoundError):
            update_customer_delivery_preference(session, 99999, "OFFICE")
