"""Tests for the customer-360 and latest-service-request MCP tools."""
from __future__ import annotations

import asyncio
import json
from datetime import date

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import mcp_server.server as mcp_server_module
from db.models import Account, Base, CardProduct, Customer, Customer360, ServiceRequest
from mcp_server.server import fetch_customer_360, fetch_latest_service_request, mcp

ACCOUNT_NUMBER = "8552"


@pytest.fixture
def seeded_session_factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'mcp_customer_360_test.db'}")
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    with factory() as session:
        session.add(Account(account_number=ACCOUNT_NUMBER))
        customer = Customer(
            account_number=ACCOUNT_NUMBER,
            full_name="Vipul Singh",
            registered_email="work@email.com",
            relationship_tier="PRIORITY",
        )
        credit_product = CardProduct(
            name="Global Elite zero forex markup credit card",
            network="Visa",
            card_type="credit",
            forex_markup_pct=0,
            external_product_id="prod-2",
        )
        session.add_all([customer, credit_product])
        session.flush()

        session.add(
            ServiceRequest(
                customer_id=customer.id,
                service_request_id="SR1156788-20261001",
                service_request_type="account_statement",
                service_request_date=date(2026, 10, 1),
                service_request_status="under progress",
                service_request_delivery_address_type="Bank Branch",
            )
        )
        session.add(
            Customer360(
                customer_id=customer.id,
                account_number=ACCOUNT_NUMBER,
                customer_name="VIPUL SINGH",
                onboarding_date=date(2023, 1, 15),
                email_work="work@email.com",
                email_personal="personal@email.com",
                addresses_json=json.dumps([{"address_type": "Correspondence", "address": "Noida"}]),
                current_instruments_json=json.dumps([{"instrument_type": "debit_card"}]),
                relationship_tier=3,
                home_branch_name="HDFC Bank",
                home_branch_address="Sita commercial complex, New Delhi",
                raw_json=json.dumps({"customer_name": "VIPUL SINGH"}),
            )
        )
        session.commit()

    return factory


def test_fetch_customer_360(seeded_session_factory):
    row = fetch_customer_360(ACCOUNT_NUMBER, session_factory=seeded_session_factory)
    assert row["customer_name"] == "VIPUL SINGH"
    assert "next_best_offer" not in row
    assert row["relationship_tier"] == 3
    assert row["home_branch"]["name"] == "HDFC Bank"
    assert row["email_work"] == "work@email.com"


def test_fetch_customer_360_unknown_account_raises_tool_error(seeded_session_factory):
    with pytest.raises(ToolError):
        fetch_customer_360("unknown", session_factory=seeded_session_factory)


def test_fetch_latest_service_request(seeded_session_factory):
    row = fetch_latest_service_request(ACCOUNT_NUMBER, session_factory=seeded_session_factory)
    assert row["service_request_id"] == "SR1156788-20261001"
    assert row["service_request_status"] == "under progress"


def test_fetch_latest_service_request_unknown_account_raises_tool_error(seeded_session_factory):
    with pytest.raises(ToolError):
        fetch_latest_service_request("unknown", session_factory=seeded_session_factory)


def test_mcp_tool_get_customer_360_end_to_end(seeded_session_factory, monkeypatch):
    monkeypatch.setattr(mcp_server_module, "SessionLocal", seeded_session_factory)

    result = asyncio.run(mcp.call_tool("get_customer_360", {"account_number": ACCOUNT_NUMBER}))

    assert result.is_error is False
    assert result.structured_content["customer_name"] == "VIPUL SINGH"


def test_mcp_tool_get_latest_service_request_end_to_end(seeded_session_factory, monkeypatch):
    monkeypatch.setattr(mcp_server_module, "SessionLocal", seeded_session_factory)

    result = asyncio.run(mcp.call_tool("get_latest_service_request", {"account_number": ACCOUNT_NUMBER}))

    assert result.is_error is False
    assert result.structured_content["service_request_id"] == "SR1156788-20261001"
