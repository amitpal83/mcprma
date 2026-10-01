"""Tests for the card & card-product catalogue MCP tools (Step 3).

Same two levels as tests/test_mcp_server.py: direct fetch_*() function tests
with an injected session_factory, then in-process mcp.call_tool() tests with
SessionLocal monkeypatched.
"""
from __future__ import annotations

import asyncio

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import mcp_server.server as mcp_server_module
from api.repository.cards import encode_reward_transfer_partners
from db.models import Account, Base, Card, CardProduct
from mcp_server.server import (
    fetch_card,
    fetch_card_product,
    fetch_card_products,
    fetch_cards_for_account,
    mcp,
)

ACCOUNT_NUMBER = "8552"


@pytest.fixture
def seeded_session_factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'mcp_cards_test.db'}")
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    with factory() as session:
        session.add(Account(account_number=ACCOUNT_NUMBER))

        debit_product = CardProduct(
            name="HDFC Debit Card", network="Visa", card_type="debit", forex_markup_pct=3.5
        )
        credit_product = CardProduct(
            name="Global Elite Zero Forex Card",
            network="Visa",
            card_type="credit",
            forex_markup_pct=0,
            reward_transfer_partners=encode_reward_transfer_partners(["Flying Blue"]),
        )
        session.add_all([debit_product, credit_product])
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

    return factory


def test_fetch_cards_for_account_returns_json_serializable_rows(seeded_session_factory):
    rows = fetch_cards_for_account(ACCOUNT_NUMBER, session_factory=seeded_session_factory)
    assert len(rows) == 1
    assert rows[0]["last4"] == "4821"


def test_fetch_cards_for_account_unknown_account_raises_tool_error(seeded_session_factory):
    with pytest.raises(ToolError, match="Account not found"):
        fetch_cards_for_account("unknown", session_factory=seeded_session_factory)


def test_fetch_card_unknown_id_raises_tool_error(seeded_session_factory):
    with pytest.raises(ToolError, match="Card not found"):
        fetch_card(99999, session_factory=seeded_session_factory)


def test_fetch_card_products_filters_by_type(seeded_session_factory):
    rows = fetch_card_products(card_type="credit", session_factory=seeded_session_factory)
    assert len(rows) == 1
    assert rows[0]["name"] == "Global Elite Zero Forex Card"
    assert rows[0]["reward_transfer_partners"] == ["Flying Blue"]


def test_fetch_card_product_unknown_id_raises_tool_error(seeded_session_factory):
    with pytest.raises(ToolError, match="Card product not found"):
        fetch_card_product(99999, session_factory=seeded_session_factory)


def test_mcp_tool_list_account_cards_end_to_end(seeded_session_factory, monkeypatch):
    monkeypatch.setattr(mcp_server_module, "SessionLocal", seeded_session_factory)

    result = asyncio.run(mcp.call_tool("list_account_cards", {"account_number": ACCOUNT_NUMBER}))

    assert result.is_error is False
    rows = result.structured_content["result"]
    assert len(rows) == 1
    assert rows[0]["last4"] == "4821"


def test_mcp_tool_get_card_unknown_id_raises_tool_error(seeded_session_factory, monkeypatch):
    monkeypatch.setattr(mcp_server_module, "SessionLocal", seeded_session_factory)

    with pytest.raises(ToolError):
        asyncio.run(mcp.call_tool("get_card", {"card_id": 99999}))


def test_mcp_tool_list_card_products_end_to_end(seeded_session_factory, monkeypatch):
    monkeypatch.setattr(mcp_server_module, "SessionLocal", seeded_session_factory)

    result = asyncio.run(mcp.call_tool("list_card_products", {"card_type": "debit"}))

    assert result.is_error is False
    rows = result.structured_content["result"]
    assert len(rows) == 1
    assert rows[0]["name"] == "HDFC Debit Card"


def test_mcp_tool_get_card_end_to_end(seeded_session_factory, monkeypatch):
    monkeypatch.setattr(mcp_server_module, "SessionLocal", seeded_session_factory)
    card_id = fetch_cards_for_account(ACCOUNT_NUMBER, session_factory=seeded_session_factory)[0]["id"]

    result = asyncio.run(mcp.call_tool("get_card", {"card_id": card_id}))

    assert result.is_error is False
    # dict[str, Any]-returning tools are NOT wrapped under "result" the way
    # list[dict]-returning tools are -- the dict itself IS structured_content.
    assert result.structured_content["last4"] == "4821"
