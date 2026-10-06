"""Tests for the MCP server.

Covers two levels:
  - fetch_account_transactions(): the plain function backing the tool,
    tested directly with an injected session_factory (same DI pattern used
    in tests/test_excel_importer.py and tests/test_api.py).
  - the registered get_account_txn_details MCP tool itself, called in-process
    via MCPServer.call_tool() (no network transport involved) to confirm the
    tool is wired up correctly end-to-end.
"""
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
from mcp_server.server import fetch_account_transactions, mcp

ACCOUNT_NUMBER = "8552"


@pytest.fixture
def seeded_session_factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'mcp_test.db'}")
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    with factory() as session:
        session.add(Account(account_number=ACCOUNT_NUMBER))
        session.add(
            Transaction(
                txn_currency="INR",
                account_number=ACCOUNT_NUMBER,
                txn_date=date(2026, 9, 5),
                merchant="TEST-TXN",
                reference_no="REF100",
                txn_amount_INR=Decimal("42.00"),
            )
        )
        session.commit()

    return factory


def test_fetch_account_transactions_returns_json_serializable_rows(seeded_session_factory):
    rows = fetch_account_transactions(
        ACCOUNT_NUMBER, "2026-09-01", "2026-09-30", session_factory=seeded_session_factory
    )

    assert len(rows) == 1
    assert rows[0]["reference_no"] == "REF100"
    assert rows[0]["txn_amount_INR"] == "42.00"  # Decimal -> string in JSON mode


def test_fetch_account_transactions_unknown_account_raises_tool_error(seeded_session_factory):
    with pytest.raises(ToolError, match="Account not found"):
        fetch_account_transactions("unknown", "2026-09-01", "2026-09-30", session_factory=seeded_session_factory)


def test_fetch_account_transactions_invalid_range_raises_tool_error(seeded_session_factory):
    with pytest.raises(ToolError, match="must not be after"):
        fetch_account_transactions(ACCOUNT_NUMBER, "2026-09-30", "2026-09-01", session_factory=seeded_session_factory)


def test_fetch_account_transactions_bad_date_format_raises_tool_error(seeded_session_factory):
    with pytest.raises(ToolError, match="ISO date"):
        fetch_account_transactions(ACCOUNT_NUMBER, "not-a-date", "2026-09-30", session_factory=seeded_session_factory)


def test_mcp_tool_success_end_to_end(seeded_session_factory, monkeypatch):
    monkeypatch.setattr(mcp_server_module, "SessionLocal", seeded_session_factory)

    result = asyncio.run(
        mcp.call_tool(
            "get_account_txn_details",
            {"account_number": ACCOUNT_NUMBER, "from_date": "2026-09-01", "to_date": "2026-09-30"},
        )
    )

    assert result.is_error is False
    rows = result.structured_content["result"]
    assert len(rows) == 1
    assert rows[0]["reference_no"] == "REF100"


def test_mcp_tool_unknown_account_raises_tool_error(seeded_session_factory, monkeypatch):
    monkeypatch.setattr(mcp_server_module, "SessionLocal", seeded_session_factory)

    with pytest.raises(ToolError):
        asyncio.run(
            mcp.call_tool(
                "get_account_txn_details",
                {"account_number": "unknown", "from_date": "2026-09-01", "to_date": "2026-09-30"},
            )
        )
