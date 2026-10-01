"""Tests for the customer profile / delivery-preference MCP tools (Step 5)."""
from __future__ import annotations

import asyncio

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import mcp_server.server as mcp_server_module
from db.models import Account, Base, Customer
from mcp_server.server import (
    fetch_customer_by_account,
    fetch_update_customer_delivery_preference,
    mcp,
)

ACCOUNT_NUMBER = "8552"


@pytest.fixture
def seeded_session_factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'mcp_customers_test.db'}")
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
        session.commit()

    return factory


def test_fetch_customer_by_account_masks_email(seeded_session_factory):
    row = fetch_customer_by_account(ACCOUNT_NUMBER, session_factory=seeded_session_factory)
    assert row["full_name"] == "Mr. Mehta"
    assert row["registered_email_masked"] == "ram*****@gmail.com"
    assert "registered_email" not in row


def test_fetch_customer_by_account_unknown_raises_tool_error(seeded_session_factory):
    with pytest.raises(ToolError, match="Customer not found"):
        fetch_customer_by_account("unknown", session_factory=seeded_session_factory)


def test_fetch_update_customer_delivery_preference_persists(seeded_session_factory):
    customer_row = fetch_customer_by_account(ACCOUNT_NUMBER, session_factory=seeded_session_factory)
    updated = fetch_update_customer_delivery_preference(
        customer_row["id"], "HOME", session_factory=seeded_session_factory
    )
    assert updated["preferred_delivery_address_type"] == "HOME"


def test_fetch_update_customer_delivery_preference_invalid_value_raises_tool_error(seeded_session_factory):
    customer_row = fetch_customer_by_account(ACCOUNT_NUMBER, session_factory=seeded_session_factory)
    with pytest.raises(ToolError, match="preferred_delivery_address_type"):
        fetch_update_customer_delivery_preference(
            customer_row["id"], "WAREHOUSE", session_factory=seeded_session_factory
        )


def test_mcp_tool_get_customer_profile_end_to_end(seeded_session_factory, monkeypatch):
    monkeypatch.setattr(mcp_server_module, "SessionLocal", seeded_session_factory)

    result = asyncio.run(mcp.call_tool("get_customer_profile", {"account_number": ACCOUNT_NUMBER}))

    assert result.is_error is False
    assert result.structured_content["registered_email_masked"] == "ram*****@gmail.com"


def test_mcp_tool_get_customer_profile_unknown_account_raises_tool_error(seeded_session_factory, monkeypatch):
    monkeypatch.setattr(mcp_server_module, "SessionLocal", seeded_session_factory)

    with pytest.raises(ToolError):
        asyncio.run(mcp.call_tool("get_customer_profile", {"account_number": "unknown"}))
