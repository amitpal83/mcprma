"""Tests for the card recommendation MCP tool (Step 7)."""
from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import mcp_server.server as mcp_server_module
from db.models import Account, Base, Card, CardProduct, Customer, Transaction
from mcp_server.server import fetch_card_recommendation, mcp

ACCOUNT_NUMBER = "8552"


@pytest.fixture
def seeded_session_factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'mcp_recs_test.db'}")
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    with factory() as session:
        session.add(Account(account_number=ACCOUNT_NUMBER))
        session.add(
            Customer(
                account_number=ACCOUNT_NUMBER,
                full_name="Mr. Mehta",
                registered_email="rammehta@gmail.com",
                relationship_tier="PRIORITY",
            )
        )
        debit_product = CardProduct(name="HDFC Debit Card", network="Visa", card_type="debit", forex_markup_pct=3.5)
        zero_forex_product = CardProduct(
            name="Global Elite Zero Forex Card",
            network="Visa",
            card_type="credit",
            forex_markup_pct=0,
            joining_fee=Decimal("15000.00"),
            relationship_discount_pct=Decimal("25.00"),
            min_relationship_tier_for_discount="PRIORITY",
        )
        session.add_all([debit_product, zero_forex_product])
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

        session.add(
            Transaction(
                account_number=ACCOUNT_NUMBER,
                card_id=card.id,
                txn_date=date.today(),
                value_date=date.today(),
                narration="WISDOM PROPERTY NL II",
                withdrawal_amount=Decimal("32000.00"),
                closing_balance=Decimal("100000.00"),
                txn_currency="EUR",
                txn_amount=Decimal("353.00"),
                forex_markup_amount=Decimal("1120.00"),
                gst_on_markup=Decimal("201.60"),
                category="Travel",
            )
        )
        session.commit()
        session.refresh(card)

    return factory, card.id


def test_fetch_card_recommendation(seeded_session_factory):
    factory, card_id = seeded_session_factory
    recommendation = fetch_card_recommendation(card_id, session_factory=factory)
    assert recommendation["recommended_product"]["name"] == "Global Elite Zero Forex Card"
    assert recommendation["discount_pct_applied"] == "25.00"
    assert recommendation["action_type"] == "cross_sell"
    assert recommendation["applicable_discounts"] == "25% on joining fee"
    assert recommendation["reason"]


def test_fetch_card_recommendation_unknown_card_raises_tool_error(seeded_session_factory):
    factory, _ = seeded_session_factory
    with pytest.raises(ToolError, match="Card not found"):
        fetch_card_recommendation(99999, session_factory=factory)


def test_mcp_tool_get_card_recommendation_end_to_end(seeded_session_factory, monkeypatch):
    factory, card_id = seeded_session_factory
    monkeypatch.setattr(mcp_server_module, "SessionLocal", factory)

    result = asyncio.run(mcp.call_tool("get_card_recommendation", {"card_id": card_id}))

    assert result.is_error is False
    assert result.structured_content["recommended_product"]["name"] == "Global Elite Zero Forex Card"
