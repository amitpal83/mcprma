"""Tests for api.repository.applications (Step 8)."""
from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.repository.applications import (
    CardApplicationNotFoundError,
    DuplicateApplicationError,
    create_card_application,
    get_card_application_status,
)
from api.repository.customers import CustomerNotFoundError
from api.repository.cards import CardProductNotFoundError
from db.models import Account, Base, CardProduct, Customer

ACCOUNT_NUMBER = "8552"


@pytest.fixture
def session_factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _seed(session, relationship_tier: str) -> tuple[int, int]:
    session.add(Account(account_number=ACCOUNT_NUMBER))
    customer = Customer(
        account_number=ACCOUNT_NUMBER,
        full_name="Mr. Mehta",
        registered_email="rammehta@gmail.com",
        relationship_tier=relationship_tier,
    )
    product = CardProduct(
        name="Global Elite Zero Forex Card",
        network="Visa",
        card_type="credit",
        forex_markup_pct=0,
        joining_fee=Decimal("15000.00"),
        relationship_discount_pct=Decimal("25.00"),
        min_relationship_tier_for_discount="PRIORITY",
    )
    session.add_all([customer, product])
    session.commit()
    return customer.id, product.id


@pytest.fixture
def seeded_priority(session_factory):
    with session_factory() as session:
        customer_id, product_id = _seed(session, "PRIORITY")
    return session_factory, customer_id, product_id


@pytest.fixture
def seeded_standard(session_factory):
    with session_factory() as session:
        customer_id, product_id = _seed(session, "STANDARD")
    return session_factory, customer_id, product_id


def test_create_application_applies_discount_for_matching_tier(seeded_priority):
    factory, customer_id, product_id = seeded_priority
    with factory() as session:
        application = create_card_application(session, customer_id, product_id, delivery_address="Office, MG Road")
        assert application.status == "SUBMITTED"
        assert application.discount_pct_applied == Decimal("25.00")
        # 15000 * 0.75 = 11250; 11250 * 1.18 = 13275.00
        assert application.fee_charged == Decimal("13275.00")
        assert application.delivery_address == "Office, MG Road"


def test_create_application_no_discount_for_non_matching_tier(seeded_standard):
    factory, customer_id, product_id = seeded_standard
    with factory() as session:
        application = create_card_application(session, customer_id, product_id)
        assert application.discount_pct_applied is None
        assert application.fee_charged == Decimal("17700.00")


def test_create_application_unknown_customer_raises(seeded_priority):
    factory, _, product_id = seeded_priority
    with factory() as session:
        with pytest.raises(CustomerNotFoundError):
            create_card_application(session, 99999, product_id)


def test_create_application_unknown_product_raises(seeded_priority):
    factory, customer_id, _ = seeded_priority
    with factory() as session:
        with pytest.raises(CardProductNotFoundError):
            create_card_application(session, customer_id, 99999)


def test_duplicate_submitted_application_rejected(seeded_priority):
    factory, customer_id, product_id = seeded_priority
    with factory() as session:
        create_card_application(session, customer_id, product_id)
        with pytest.raises(DuplicateApplicationError):
            create_card_application(session, customer_id, product_id)


def test_new_application_allowed_after_prior_is_rejected(seeded_priority):
    factory, customer_id, product_id = seeded_priority
    with factory() as session:
        first = create_card_application(session, customer_id, product_id)
        first.status = "REJECTED"
        session.commit()

        second = create_card_application(session, customer_id, product_id)
        assert second.status == "SUBMITTED"
        assert second.id != first.id


def test_get_card_application_status(seeded_priority):
    factory, customer_id, product_id = seeded_priority
    with factory() as session:
        application_id = create_card_application(session, customer_id, product_id).id

    with factory() as session:
        application = get_card_application_status(session, application_id)
        assert application.id == application_id


def test_get_unknown_application_raises(seeded_priority):
    factory, _, _ = seeded_priority
    with factory() as session:
        with pytest.raises(CardApplicationNotFoundError):
            get_card_application_status(session, 99999)
