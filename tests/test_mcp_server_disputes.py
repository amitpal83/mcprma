"""Tests for the dispute MCP tools (Step 6)."""
from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import mcp_server.server as mcp_server_module
from db.models import Account, Base, Transaction
from mcp_server.server import fetch_create_dispute, fetch_withdraw_dispute, mcp

ACCOUNT_NUMBER = "8552"


@pytest.fixture
def seeded_session_factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'mcp_disputes_test.db'}")
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    with factory() as session:
        session.add(Account(account_number=ACCOUNT_NUMBER))
        session.add(
            Transaction(
                account_number=ACCOUNT_NUMBER,
                txn_date=date(2026, 8, 12),
                value_date=date(2026, 8, 12),
                merchant="WISDOM PROPERTY NL II",
                withdrawal_amount=Decimal("32000.00"),
                closing_balance=Decimal("100000.00"),
            )
        )
        session.commit()
        transaction_id = session.query(Transaction).filter_by(account_number=ACCOUNT_NUMBER).first().id

    return factory, transaction_id


def test_fetch_create_dispute(seeded_session_factory):
    factory, transaction_id = seeded_session_factory
    dispute = fetch_create_dispute(transaction_id, "Unrecognized charge", session_factory=factory)
    assert dispute["status"] == "OPEN"


def test_fetch_create_dispute_unknown_transaction_raises_tool_error(seeded_session_factory):
    factory, _ = seeded_session_factory
    with pytest.raises(ToolError, match="Transaction not found"):
        fetch_create_dispute(99999, "Unrecognized charge", session_factory=factory)


def test_fetch_withdraw_dispute(seeded_session_factory):
    factory, transaction_id = seeded_session_factory
    dispute = fetch_create_dispute(transaction_id, "Unrecognized charge", session_factory=factory)

    withdrawn = fetch_withdraw_dispute(dispute["id"], session_factory=factory)
    assert withdrawn["status"] == "WITHDRAWN"


def test_mcp_tool_create_dispute_end_to_end(seeded_session_factory, monkeypatch):
    factory, transaction_id = seeded_session_factory
    monkeypatch.setattr(mcp_server_module, "SessionLocal", factory)

    result = asyncio.run(
        mcp.call_tool("create_dispute", {"transaction_id": transaction_id, "reason": "Unrecognized charge"})
    )

    assert result.is_error is False
    assert result.structured_content["status"] == "OPEN"


def test_mcp_tool_withdraw_dispute_end_to_end(seeded_session_factory, monkeypatch):
    factory, transaction_id = seeded_session_factory
    monkeypatch.setattr(mcp_server_module, "SessionLocal", factory)
    dispute = fetch_create_dispute(transaction_id, "Unrecognized charge", session_factory=factory)

    result = asyncio.run(mcp.call_tool("withdraw_dispute", {"dispute_id": dispute["id"]}))

    assert result.is_error is False
    assert result.structured_content["status"] == "WITHDRAWN"


def test_mcp_tool_create_dispute_duplicate_raises_tool_error(seeded_session_factory, monkeypatch):
    factory, transaction_id = seeded_session_factory
    monkeypatch.setattr(mcp_server_module, "SessionLocal", factory)
    fetch_create_dispute(transaction_id, "Unrecognized charge", session_factory=factory)

    with pytest.raises(ToolError):
        asyncio.run(mcp.call_tool("create_dispute", {"transaction_id": transaction_id, "reason": "Again"}))
