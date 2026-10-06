"""Tests for api.repository.cards (Step 3: catalogue lookups, read-only)."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.repository.cards import (
    CardNotFoundError,
    CardProductNotFoundError,
    decode_reward_transfer_partners,
    encode_reward_transfer_partners,
    get_card,
    get_card_product,
    list_card_products,
    list_cards_for_account,
)
from api.repository.transactions import AccountNotFoundError
from db.models import Account, Base, Card, CardProduct

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

        debit_product = CardProduct(
            name="HDFC Debit Card",
            network="Visa",
            card_type="debit",
            forex_markup_pct=3.5,
        )
        credit_product = CardProduct(
            name="Global Elite Zero Forex Card",
            network="Visa",
            card_type="credit",
            forex_markup_pct=0,
            joining_fee=15000,
            annual_fee=15000,
            lounge_visits_domestic_per_year=None,
            lounge_visits_international_per_year=None,
            reward_transfer_partners=encode_reward_transfer_partners(
                ["Air India Maharaja Club", "Flying Blue", "Accor ALL"]
            ),
        )
        inactive_product = CardProduct(
            name="Discontinued Card",
            network="Visa",
            card_type="credit",
            forex_markup_pct=2.0,
            is_active=False,
        )
        session.add_all([debit_product, credit_product, inactive_product])
        session.flush()

        session.add(
            Card(
                account_number=ACCOUNT_NUMBER,
                card_product_id=debit_product.id,
                last4="4821",
                network="Visa",
                card_type="debit",
            )
        )
        session.commit()

    return session_factory


def test_list_cards_for_account_returns_cards(seeded_session_factory):
    with seeded_session_factory() as session:
        cards = list_cards_for_account(session, ACCOUNT_NUMBER)
        assert len(cards) == 1
        assert cards[0].last4 == "4821"


def test_list_cards_for_unknown_account_raises(seeded_session_factory):
    with seeded_session_factory() as session:
        with pytest.raises(AccountNotFoundError):
            list_cards_for_account(session, "unknown")


def test_get_card_returns_card(seeded_session_factory):
    with seeded_session_factory() as session:
        card = list_cards_for_account(session, ACCOUNT_NUMBER)[0]
        fetched = get_card(session, card.id)
        assert fetched.id == card.id


def test_get_unknown_card_raises(seeded_session_factory):
    with seeded_session_factory() as session:
        with pytest.raises(CardNotFoundError):
            get_card(session, 99999)


def test_list_card_products_active_only_by_default(seeded_session_factory):
    with seeded_session_factory() as session:
        products = list_card_products(session)
        names = {p.name for p in products}
        assert "Discontinued Card" not in names
        assert "Global Elite Zero Forex Card" in names


def test_list_card_products_filters_by_card_type(seeded_session_factory):
    with seeded_session_factory() as session:
        products = list_card_products(session, card_type="credit")
        assert all(p.card_type == "credit" for p in products)
        assert any(p.name == "Global Elite Zero Forex Card" for p in products)


def test_list_card_products_active_only_false_includes_inactive(seeded_session_factory):
    with seeded_session_factory() as session:
        products = list_card_products(session, active_only=False)
        names = {p.name for p in products}
        assert "Discontinued Card" in names


def test_get_card_product_decodes_reward_partners(seeded_session_factory):
    with seeded_session_factory() as session:
        products = list_card_products(session, card_type="credit")
        credit_product = next(p for p in products if p.name == "Global Elite Zero Forex Card")

        product = get_card_product(session, credit_product.id)
        partners = decode_reward_transfer_partners(product.reward_transfer_partners)
        assert partners == ["Air India Maharaja Club", "Flying Blue", "Accor ALL"]


def test_get_unknown_card_product_raises(seeded_session_factory):
    with seeded_session_factory() as session:
        with pytest.raises(CardProductNotFoundError):
            get_card_product(session, 99999)
