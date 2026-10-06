"""Tests for the account-scoped card-transaction search / forex-summary /
category-breakdown MCP tools: search_account_transactions,
get_account_forex_summary, get_account_category_breakdown (Step 4; renamed
from their original card_id-scoped names in a later pass -- each tool
resolves every card linked to the account internally instead of taking a
card_id)."""
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
    fetch_account_card_transactions,
    fetch_account_category_breakdown,
    fetch_account_forex_summary,
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
                    txn_date=date(2026, 8, 12),
                    merchant="WISDOM PROPERTY NL II",
                    reference_no="SEED-FX-001",
                    txn_amount_INR=Decimal("32000.00"),
                    txn_currency="EUR",
                    txn_amount=Decimal("353.00"),
                    forex_markup_amount_INR=Decimal("1120.00"),
                    category="Travel",
                    transaction_type="international",
                ),
                Transaction(
                    txn_currency="INR",
                    account_number=ACCOUNT_NUMBER,
                    card_id=card.id,
                    txn_date=date(2026, 7, 1),
                    merchant="BIG BAZAAR MUMBAI",
                    reference_no="SEED-FX-002",
                    txn_amount_INR=Decimal("500.00"),
                    category="Groceries",
                    transaction_type="domestic",
                ),
            ]
        )
        session.commit()
        session.refresh(card)

    return factory


@pytest.fixture
def seeded_two_card_session_factory(tmp_path):
    """An account with TWO cards, each with its own forex transaction --
    exercises the aggregation path these tools now need."""
    engine = create_engine(f"sqlite:///{tmp_path / 'mcp_forex_two_card_test.db'}")
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    with factory() as session:
        session.add(Account(account_number=ACCOUNT_NUMBER))
        debit_product = CardProduct(name="HDFC Debit Card", network="Visa", card_type="debit", forex_markup_pct=3.5)
        credit_product = CardProduct(name="Travel Credit Card", network="Visa", card_type="credit", forex_markup_pct=2.0)
        session.add_all([debit_product, credit_product])
        session.flush()

        debit_card = Card(
            account_number=ACCOUNT_NUMBER,
            card_product_id=debit_product.id,
            last4="4821",
            network="Visa",
            card_type="debit",
        )
        credit_card = Card(
            account_number=ACCOUNT_NUMBER,
            card_product_id=credit_product.id,
            last4="1234",
            network="Visa",
            card_type="credit",
        )
        session.add_all([debit_card, credit_card])
        session.flush()

        session.add_all(
            [
                Transaction(
                    account_number=ACCOUNT_NUMBER,
                    card_id=debit_card.id,
                    txn_date=date(2026, 8, 1),
                    merchant="DEBIT CARD FOREX SPEND",
                    reference_no="DEBIT-001",
                    txn_amount_INR=Decimal("10000.00"),
                    txn_currency="USD",
                    txn_amount=Decimal("120.00"),
                    forex_markup_amount_INR=Decimal("350.00"),
                    category="Travel",
                    transaction_type="international",
                ),
                Transaction(
                    account_number=ACCOUNT_NUMBER,
                    card_id=credit_card.id,
                    txn_date=date(2026, 8, 5),
                    merchant="CREDIT CARD FOREX SPEND",
                    reference_no="CREDIT-001",
                    txn_amount_INR=Decimal("5000.00"),
                    txn_currency="USD",
                    txn_amount=Decimal("60.00"),
                    forex_markup_amount_INR=Decimal("100.00"),
                    category="Travel",
                    transaction_type="international",
                ),
            ]
        )
        session.commit()

    return factory


def test_fetch_account_card_transactions_finds_disputed_txn(seeded_session_factory):
    factory = seeded_session_factory
    rows = fetch_account_card_transactions(
        ACCOUNT_NUMBER, "2026-08-01", "2026-08-31", amount_min="340", amount_max="360", session_factory=factory
    )
    assert len(rows) == 1
    assert rows[0]["reference_no"] == "SEED-FX-001"


def test_fetch_account_card_transactions_unknown_account_raises_tool_error(seeded_session_factory):
    factory = seeded_session_factory
    with pytest.raises(ToolError, match="Account not found"):
        fetch_account_card_transactions("unknown", "2026-01-01", "2026-12-31", session_factory=factory)


def test_fetch_account_card_transactions_bad_amount_raises_tool_error(seeded_session_factory):
    factory = seeded_session_factory
    with pytest.raises(ToolError, match="decimal number"):
        fetch_account_card_transactions(
            ACCOUNT_NUMBER, "2026-01-01", "2026-12-31", amount_min="not-a-number", session_factory=factory
        )


def test_fetch_account_card_transactions_reversed_amount_range_raises_tool_error(seeded_session_factory):
    """amount_min > amount_max previously matched nothing silently instead of
    raising -- e.g. a voice agent passing ("39000", "21000") for "around
    30000" got a confusing empty result instead of a clear error."""
    factory = seeded_session_factory
    with pytest.raises(ToolError, match="amount_min"):
        fetch_account_card_transactions(
            ACCOUNT_NUMBER,
            "2026-01-01",
            "2026-12-31",
            amount_min="39000",
            amount_max="21000",
            session_factory=factory,
        )


def test_fetch_account_card_transactions_spans_every_card_on_account(seeded_two_card_session_factory):
    rows = fetch_account_card_transactions(
        ACCOUNT_NUMBER, "2026-01-01", "2026-12-31", session_factory=seeded_two_card_session_factory
    )
    reference_numbers = {row["reference_no"] for row in rows}
    assert reference_numbers == {"DEBIT-001", "CREDIT-001"}


def test_fetch_account_forex_summary(seeded_session_factory):
    factory = seeded_session_factory
    summary = fetch_account_forex_summary(ACCOUNT_NUMBER, AS_OF, session_factory=factory)
    assert summary["transaction_count"] == 1
    assert summary["total_forex_spend_inr"] == "32000.00"


def test_fetch_account_forex_summary_aggregates_every_card_on_account(seeded_two_card_session_factory):
    summary = fetch_account_forex_summary(
        ACCOUNT_NUMBER, "2026-09-30", session_factory=seeded_two_card_session_factory
    )
    assert summary["transaction_count"] == 2
    assert summary["total_forex_spend_inr"] == "15000.00"
    assert summary["total_markup_amount"] == "450.00"


def test_fetch_account_category_breakdown(seeded_session_factory):
    factory = seeded_session_factory

    international = fetch_account_category_breakdown(
        ACCOUNT_NUMBER, "2026-01-01", "2026-12-31", "international", session_factory=factory
    )
    assert international["total_amount"] == "32000.00"
    assert international["total_forex_markup_amount_INR"] == "1120.00"
    assert international["transaction_count"] == 1

    domestic = fetch_account_category_breakdown(
        ACCOUNT_NUMBER, "2026-01-01", "2026-12-31", "domestic", session_factory=factory
    )
    assert domestic["total_amount"] == "500.00"
    assert domestic["total_forex_markup_amount_INR"] == "0"
    assert domestic["transaction_count"] == 1

    scoped_to_groceries = fetch_account_category_breakdown(
        ACCOUNT_NUMBER, "2026-01-01", "2026-12-31", "domestic", "Groceries", session_factory=factory
    )
    assert scoped_to_groceries["transaction_count"] == 1

    scoped_to_travel = fetch_account_category_breakdown(
        ACCOUNT_NUMBER, "2026-01-01", "2026-12-31", "domestic", "Travel", session_factory=factory
    )
    assert scoped_to_travel["transaction_count"] == 0


def test_fetch_account_category_breakdown_aggregates_every_card_on_account(seeded_two_card_session_factory):
    result = fetch_account_category_breakdown(
        ACCOUNT_NUMBER, "2026-01-01", "2026-12-31", "international", session_factory=seeded_two_card_session_factory
    )
    assert result["total_amount"] == "15000.00"
    assert result["total_forex_markup_amount_INR"] == "450.00"
    assert result["transaction_count"] == 2


def test_mcp_tool_search_account_transactions_end_to_end(seeded_session_factory, monkeypatch):
    factory = seeded_session_factory
    monkeypatch.setattr(mcp_server_module, "SessionLocal", factory)

    result = asyncio.run(
        mcp.call_tool(
            "search_account_transactions",
            {
                "account_number": ACCOUNT_NUMBER,
                "from_date": "2026-08-01",
                "to_date": "2026-08-31",
                "merchant_text": "Wisdom Property",
            },
        )
    )

    assert result.is_error is False
    rows = result.structured_content["result"]
    assert len(rows) == 1
    assert rows[0]["reference_no"] == "SEED-FX-001"


def test_mcp_tool_search_account_transactions_empty_match_still_produces_content(seeded_session_factory, monkeypatch):
    """Regression test: a list[dict]-returning MCP tool that finds zero
    matches used to produce a CallToolResult with content=[] (no text
    blocks), even though structured_content correctly held {"result": []}.
    Some MCP clients (e.g. LiveKit's default tool-result resolver) only read
    `content` and treat an empty one as "the tool produced nothing",
    surfacing a confusing error for a legitimate "no matches" answer. The
    fix wraps this tool's return as a dict so it always emits exactly one
    text content block, empty result or not.
    """
    factory = seeded_session_factory
    monkeypatch.setattr(mcp_server_module, "SessionLocal", factory)

    result = asyncio.run(
        mcp.call_tool(
            "search_account_transactions",
            {
                "account_number": ACCOUNT_NUMBER,
                "from_date": "2026-08-01",
                "to_date": "2026-08-31",
                "amount_min": "39000",
                "amount_max": "39000",
            },
        )
    )

    assert result.is_error is False
    assert result.structured_content == {"result": []}
    assert len(result.content) >= 1


def test_mcp_tool_get_account_forex_summary_end_to_end(seeded_session_factory, monkeypatch):
    factory = seeded_session_factory
    monkeypatch.setattr(mcp_server_module, "SessionLocal", factory)

    result = asyncio.run(
        mcp.call_tool("get_account_forex_summary", {"account_number": ACCOUNT_NUMBER, "as_of_date": AS_OF})
    )

    assert result.is_error is False
    # dict[str, Any]-returning tools are NOT wrapped under "result" the way
    # list[dict]-returning tools are -- the dict itself IS structured_content.
    assert result.structured_content["total_forex_spend_inr"] == "32000.00"


def test_mcp_tool_get_account_forex_summary_unknown_account_raises_tool_error(seeded_session_factory, monkeypatch):
    factory = seeded_session_factory
    monkeypatch.setattr(mcp_server_module, "SessionLocal", factory)

    with pytest.raises(ToolError):
        asyncio.run(mcp.call_tool("get_account_forex_summary", {"account_number": "unknown"}))


def test_mcp_tool_search_account_transactions_reversed_amount_range_is_error(seeded_session_factory, monkeypatch):
    factory = seeded_session_factory
    monkeypatch.setattr(mcp_server_module, "SessionLocal", factory)

    with pytest.raises(ToolError, match="amount_min"):
        asyncio.run(
            mcp.call_tool(
                "search_account_transactions",
                {
                    "account_number": ACCOUNT_NUMBER,
                    "from_date": "2026-01-01",
                    "to_date": "2026-12-31",
                    "amount_min": "39000",
                    "amount_max": "21000",
                },
            )
        )
