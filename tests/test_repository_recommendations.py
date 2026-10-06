"""Tests for api.repository.cards.recommend_card_upgrade (Step 7)."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.repository.cards import CardNotFoundError, NoEligibleCardProductError, recommend_card_upgrade
from db.models import Account, Base, Card, CardProduct, Customer, Transaction

ACCOUNT_NUMBER = "8552"


@pytest.fixture
def session_factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _seed(session, relationship_tier: str):
    session.add(Account(account_number=ACCOUNT_NUMBER))
    session.add(
        Customer(
            account_number=ACCOUNT_NUMBER,
            full_name="Mr. Mehta",
            registered_email="rammehta@gmail.com",
            relationship_tier=relationship_tier,
        )
    )

    debit_product = CardProduct(name="HDFC Debit Card", network="Visa", card_type="debit", forex_markup_pct=3.5)
    zero_forex_product = CardProduct(
        name="Global Elite Zero Forex Card",
        network="Visa",
        card_type="credit",
        forex_markup_pct=0,
        joining_fee=Decimal("15000.00"),
        annual_fee=Decimal("15000.00"),
    )
    higher_markup_product = CardProduct(
        name="Some Other Credit Card", network="Visa", card_type="credit", forex_markup_pct=2.0
    )
    inactive_lower_markup_product = CardProduct(
        name="Discontinued Zero Forex Card",
        network="Visa",
        card_type="credit",
        forex_markup_pct=0,
        is_active=False,
    )
    session.add_all([debit_product, zero_forex_product, higher_markup_product, inactive_lower_markup_product])
    session.flush()

    card = Card(
        account_number=ACCOUNT_NUMBER,
        card_product_id=debit_product.id,
        last4="4821",
        network="Visa",
        card_type="debit",
    )
    session.add(card)
    session.flush()

    session.add_all(
        [
            Transaction(
                account_number=ACCOUNT_NUMBER,
                card_id=card.id,
                txn_date=date.today().replace(day=1),
                merchant="WISDOM PROPERTY NL II",
                txn_amount_INR=Decimal("32000.00"),
                txn_currency="EUR",
                txn_amount=Decimal("353.00"),
                forex_markup_amount_INR=Decimal("1120.00"),
                category="Travel",
            ),
            Transaction(
                account_number=ACCOUNT_NUMBER,
                card_id=card.id,
                txn_date=date.today(),
                merchant="STARBUCKS COFFEE SG",
                txn_amount_INR=Decimal("8300.00"),
                txn_currency="USD",
                txn_amount=Decimal("100.00"),
                forex_markup_amount_INR=Decimal("290.50"),
                category="Dining",
            ),
        ]
    )
    session.commit()
    return card.id


@pytest.fixture
def seeded_priority_customer(session_factory):
    with session_factory() as session:
        card_id = _seed(session, relationship_tier="PRIORITY")
    return session_factory, card_id


def test_recommends_lowest_markup_active_credit_product(seeded_priority_customer):
    factory, card_id = seeded_priority_customer
    with factory() as session:
        recommendation = recommend_card_upgrade(session, card_id)
        assert recommendation.recommended_product.name == "Global Elite Zero Forex Card"


def test_projected_savings_matches_hand_computed_total(seeded_priority_customer):
    factory, card_id = seeded_priority_customer
    with factory() as session:
        recommendation = recommend_card_upgrade(session, card_id)
        assert recommendation.trailing_12mo_forex_spend_inr == Decimal("40300.00")
        assert recommendation.current_annual_markup == Decimal("1410.50")
        assert recommendation.projected_annual_markup == Decimal("0.00")
        assert recommendation.projected_annual_savings == Decimal("1410.50")


def test_net_joining_fee_is_joining_fee_plus_gst(seeded_priority_customer):
    """relationship_discount_pct was removed from CardProduct -- net_joining_fee
    is always the undiscounted joining_fee + GST, regardless of the
    customer's relationship tier."""
    factory, card_id = seeded_priority_customer
    with factory() as session:
        recommendation = recommend_card_upgrade(session, card_id)
        # 15000 * 1.18 (DEFAULT_GST_RATE) = 17700.00
        assert recommendation.net_joining_fee == Decimal("17700.00")


def test_action_type_is_cross_sell_from_debit_to_credit(seeded_priority_customer):
    factory, card_id = seeded_priority_customer
    with factory() as session:
        recommendation = recommend_card_upgrade(session, card_id)
        assert recommendation.action_type == "cross_sell"


def test_reason_cites_lower_markup_below_high_spend_threshold(seeded_priority_customer):
    # Fixture's trailing forex spend (40300.00) is below HIGH_FOREX_SPEND_THRESHOLD_INR (100000).
    factory, card_id = seeded_priority_customer
    with factory() as session:
        recommendation = recommend_card_upgrade(session, card_id)
        assert "Lower forex markup" in recommendation.reason


def test_reason_cites_high_forex_spending_above_threshold(seeded_priority_customer):
    factory, card_id = seeded_priority_customer
    with factory() as session:
        # Push trailing spend above the high-spend threshold.
        session.add(
            Transaction(
                account_number=ACCOUNT_NUMBER,
                card_id=card_id,
                txn_date=date.today(),
                merchant="BIG TICKET FOREX SPEND",
                txn_amount_INR=Decimal("200000.00"),
                txn_currency="USD",
                txn_amount=Decimal("2400.00"),
                forex_markup_amount_INR=Decimal("7000.00"),
                category="Shopping",
            )
        )
        session.commit()

        recommendation = recommend_card_upgrade(session, card_id)
        assert "HIGH FOREX Spending" in recommendation.reason


def test_unknown_card_raises(seeded_priority_customer):
    factory, _ = seeded_priority_customer
    with factory() as session:
        with pytest.raises(CardNotFoundError):
            recommend_card_upgrade(session, 99999)


def test_no_eligible_product_raises(session_factory):
    with session_factory() as session:
        session.add(Account(account_number=ACCOUNT_NUMBER))
        product = CardProduct(name="Zero Markup Debit", network="Visa", card_type="debit", forex_markup_pct=0)
        session.add(product)
        session.flush()
        card = Card(
            account_number=ACCOUNT_NUMBER, card_product_id=product.id, last4="9999", network="Visa", card_type="debit"
        )
        session.add(card)
        session.commit()
        card_id = card.id

    with session_factory() as session:
        with pytest.raises(NoEligibleCardProductError):
            recommend_card_upgrade(session, card_id)
