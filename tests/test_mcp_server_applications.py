"""Tests for the card application MCP tools (Step 8)."""
from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import mcp_server.server as mcp_server_module
from db.models import Account, Base, CardProduct, Customer
from mcp_server.server import fetch_card_application_status, fetch_create_card_application, mcp

ACCOUNT_NUMBER = "8552"


@pytest.fixture
def seeded_session_factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'mcp_applications_test.db'}")
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    with factory() as session:
        session.add(Account(account_number=ACCOUNT_NUMBER))
        customer = Customer(
            account_number=ACCOUNT_NUMBER,
            full_name="Mr. Mehta",
            registered_email="rammehta@gmail.com",
            relationship_tier="PRIORITY",
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
        customer_id, product_id = customer.id, product.id

    return factory, customer_id, product_id


def test_fetch_create_card_application(seeded_session_factory):
    factory, customer_id, product_id = seeded_session_factory
    application = fetch_create_card_application(customer_id, product_id, session_factory=factory)
    assert application["status"] == "SUBMITTED"
    assert application["fee_charged"] == "13275.00"


def test_fetch_create_card_application_duplicate_raises_tool_error(seeded_session_factory):
    factory, customer_id, product_id = seeded_session_factory
    fetch_create_card_application(customer_id, product_id, session_factory=factory)
    with pytest.raises(ToolError):
        fetch_create_card_application(customer_id, product_id, session_factory=factory)


def test_fetch_card_application_status(seeded_session_factory):
    factory, customer_id, product_id = seeded_session_factory
    application = fetch_create_card_application(customer_id, product_id, session_factory=factory)

    status = fetch_card_application_status(application["id"], session_factory=factory)
    assert status["status"] == "SUBMITTED"


def test_mcp_tool_create_card_application_end_to_end(seeded_session_factory, monkeypatch):
    factory, customer_id, product_id = seeded_session_factory
    monkeypatch.setattr(mcp_server_module, "SessionLocal", factory)

    result = asyncio.run(
        mcp.call_tool("create_card_application", {"customer_id": customer_id, "card_product_id": product_id})
    )

    assert result.is_error is False
    assert result.structured_content["status"] == "SUBMITTED"


def test_mcp_tool_get_card_application_status_unknown_raises_tool_error(seeded_session_factory, monkeypatch):
    factory, _, _ = seeded_session_factory
    monkeypatch.setattr(mcp_server_module, "SessionLocal", factory)

    with pytest.raises(ToolError):
        asyncio.run(mcp.call_tool("get_card_application_status", {"application_id": 99999}))
