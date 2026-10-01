"""Tests for the card-transaction search / forex-summary / category-breakdown
MCP tools (Step 4)."""
from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import mcp_server.server as mcp_server_module
from db.models import Account, Base, Card, CardProduct, Merchant, MerchantAlias, Transaction
from mcp_server.server import (
    fetch_card_category_breakdown,
    fetch_card_forex_summary,
    fetch_card_transactions,
    mcp,
)

ACCOUNT_NUMBER = "8552"
AS_OF = "2026-09-30"


@pytest.fixture
def seeded_session_factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'mcp_forex_test.db'}")
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    with factory() as session:
        session.add(Account(account_number=ACCOUNT_NUMBER))
        product = CardProduct(name="HDFC Debit Card", network="Visa", card_type="debit", forex_markup_pct=3.5)
        session.add(product)
        session.flush()

        card = Card(
            account_number=ACCOUNT_NUMBER, card_product_id=product.id, last4="4821", network="Visa", card_type="debit"
        )
        session.add(card)
        session.flush()

        merchant = Merchant(brand_name="DoubleTree by Hilton Amsterdam", city="Amsterdam", category="Travel")
        session.add(merchant)
        session.flush()
        session.add(MerchantAlias(merchant_id=merchant.id, raw_pattern="WISDOM PROPERTY NL II"))

        session.add_all(
            [
                Transaction(
                    account_number=ACCOUNT_NUMBER,
                    card_id=card.id,
                    merchant_id=merchant.id,
                    txn_date=date(2026, 8, 12),
                    value_date=date(2026, 8, 12),
                    narration="WISDOM PROPERTY NL II",
                    reference_no="SEED-FX-001",
                    withdrawal_amount=Decimal("32000.00"),
                    closing_balance=Decimal("100000.00"),
                    txn_currency="EUR",
                    txn_amount=Decimal("353.00"),
                    forex_markup_amount=Decimal("1120.00"),
                    gst_on_markup=Decimal("201.60"),
                    category="Travel",
                ),
                Transaction(
                    account_number=ACCOUNT_NUMBER,
                    card_id=card.id,
                    txn_date=date(2026, 7, 1),
                    value_date=date(2026, 7, 1),
                    narration="BIG BAZAAR MUMBAI",
                    reference_no="SEED-FX-002",
                    withdrawal_amount=Decimal("500.00"),
                    closing_balance=Decimal("85000.00"),
                    category="Groceries",
                ),
            ]
        )
        session.commit()
        session.refresh(card)

    return factory, card.id


def test_fetch_card_transactions_finds_disputed_txn(seeded_session_factory):
    factory, card_id = seeded_session_factory
    rows = fetch_card_transactions(
        card_id, "2026-08-01", "2026-08-31", amount_min="340", amount_max="360", session_factory=factory
    )
    assert len(rows) == 1
    assert rows[0]["reference_no"] == "SEED-FX-001"


def test_fetch_card_transactions_unknown_card_raises_tool_error(seeded_session_factory):
    factory, _ = seeded_session_factory
    with pytest.raises(ToolError, match="Card not found"):
        fetch_card_transactions(99999, "2026-01-01", "2026-12-31", session_factory=factory)


def test_fetch_card_transactions_bad_amount_raises_tool_error(seeded_session_factory):
    factory, card_id = seeded_session_factory
    with pytest.raises(ToolError, match="decimal number"):
        fetch_card_transactions(card_id, "2026-01-01", "2026-12-31", amount_min="not-a-number", session_factory=factory)


def test_fetch_card_forex_summary(seeded_session_factory):
    factory, card_id = seeded_session_factory
    summary = fetch_card_forex_summary(card_id, AS_OF, session_factory=factory)
    assert summary["transaction_count"] == 1
    assert summary["total_forex_spend_inr"] == "32000.00"


def test_fetch_card_category_breakdown(seeded_session_factory):
    factory, card_id = seeded_session_factory
    rows = fetch_card_category_breakdown(card_id, "2026-01-01", "2026-12-31", session_factory=factory)
    categories = {row["category"] for row in rows}
    assert categories == {"Travel", "Groceries"}


def test_mcp_tool_search_card_transactions_end_to_end(seeded_session_factory, monkeypatch):
    factory, card_id = seeded_session_factory
    monkeypatch.setattr(mcp_server_module, "SessionLocal", factory)

    result = asyncio.run(
        mcp.call_tool(
            "search_card_transactions",
            {"card_id": card_id, "from_date": "2026-08-01", "to_date": "2026-08-31", "merchant_text": "Wisdom Property"},
        )
    )

    assert result.is_error is False
    rows = result.structured_content["result"]
    assert len(rows) == 1
    assert rows[0]["reference_no"] == "SEED-FX-001"


def test_mcp_tool_get_card_forex_summary_end_to_end(seeded_session_factory, monkeypatch):
    factory, card_id = seeded_session_factory
    monkeypatch.setattr(mcp_server_module, "SessionLocal", factory)

    result = asyncio.run(mcp.call_tool("get_card_forex_summary", {"card_id": card_id, "as_of_date": AS_OF}))

    assert result.is_error is False
    # dict[str, Any]-returning tools are NOT wrapped under "result" the way
    # list[dict]-returning tools are -- the dict itself IS structured_content.
    assert result.structured_content["total_forex_spend_inr"] == "32000.00"


def test_mcp_tool_get_card_forex_summary_unknown_card_raises_tool_error(seeded_session_factory, monkeypatch):
    factory, _ = seeded_session_factory
    monkeypatch.setattr(mcp_server_module, "SessionLocal", factory)

    with pytest.raises(ToolError):
        asyncio.run(mcp.call_tool("get_card_forex_summary", {"card_id": 99999}))
