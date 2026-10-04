"""Tests for the customer-360 and latest-service-request HTTP endpoints."""
from __future__ import annotations

import json
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.dependencies import get_db
from api.main import app
from db.models import Account, Base, CardProduct, Customer, Customer360, ServiceRequest

ACCOUNT_NUMBER = "8552"


@pytest.fixture
def client(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'api_customer_360_test.db'}")
    Base.metadata.create_all(bind=engine)
    TestSessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

    with TestSessionLocal() as session:
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

        service_request = ServiceRequest(
            customer_id=customer.id,
            service_request_id="SR1156788-20261001",
            service_request_type="account_statement",
            service_request_date=date(2026, 10, 1),
            service_request_status="under progress",
            service_request_details="Dispatched via courier, expected delivery by 2026-10-07",
            service_request_delivery_address_type="Bank Branch",
        )
        session.add(service_request)
        session.add(
            Customer360(
                customer_id=customer.id,
                account_number=ACCOUNT_NUMBER,
                customer_name="VIPUL SINGH",
                onboarding_date=date(2023, 1, 15),
                email_work="work@email.com",
                email_personal="personal@email.com",
                addresses_json=json.dumps([{"address_type": "home", "address": "Noida", "preferred_flag": False}]),
                current_instruments_json=json.dumps([{"instrument_type": "debit_card"}]),
                next_best_offer_product_external_id="prod-2",
                next_best_offer_product_id=credit_product.id,
                next_best_offer_action_type="cross_sell",
                next_best_offer_applicable_discounts="25% on joining fee",
                next_best_offer_reason="HIGH FOREX Spending",
                raw_json=json.dumps({"customer_name": "VIPUL SINGH"}),
            )
        )
        session.commit()

    def override_get_db():
        session = TestSessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_get_account_customer_360(client):
    response = client.get(f"/accounts/{ACCOUNT_NUMBER}/customer-360")

    assert response.status_code == 200
    body = response.json()
    assert body["customer_name"] == "VIPUL SINGH"
    assert body["next_best_offer"]["recommended_product_id"] == "prod-2"
    assert body["next_best_offer"]["action_type"] == "cross_sell"
    assert body["next_best_offer"]["reason"] == "HIGH FOREX Spending"
    assert body["addresses"][0]["address"] == "Noida"
    assert body["email_work_masked"] == "wor***@email.com"


def test_get_account_customer_360_unknown_account_returns_404(client):
    response = client.get("/accounts/does-not-exist/customer-360")
    assert response.status_code == 404


def test_get_account_latest_service_request(client):
    response = client.get(f"/accounts/{ACCOUNT_NUMBER}/service-requests/latest")

    assert response.status_code == 200
    body = response.json()
    assert body["service_request_id"] == "SR1156788-20261001"
    assert body["service_request_status"] == "under progress"


def test_get_account_latest_service_request_unknown_account_returns_404(client):
    response = client.get("/accounts/does-not-exist/service-requests/latest")
    assert response.status_code == 404
