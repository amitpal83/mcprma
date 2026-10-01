"""Tests for the card application HTTP endpoints (Step 8)."""
from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.dependencies import get_db
from api.main import app
from db.models import Account, Base, CardProduct, Customer

ACCOUNT_NUMBER = "8552"


@pytest.fixture
def client(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'api_applications_test.db'}")
    Base.metadata.create_all(bind=engine)
    TestSessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

    with TestSessionLocal() as session:
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

    def override_get_db():
        session = TestSessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield TestClient(app), customer_id, product_id
    finally:
        app.dependency_overrides.clear()


def test_submit_and_check_application(client):
    test_client, customer_id, product_id = client

    create_response = test_client.post(
        "/card-applications",
        json={"customer_id": customer_id, "card_product_id": product_id, "delivery_address": "Office, MG Road"},
    )
    assert create_response.status_code == 201
    body = create_response.json()
    assert body["status"] == "SUBMITTED"
    assert body["fee_charged"] == "13275.00"

    status_response = test_client.get(f"/card-applications/{body['id']}")
    assert status_response.status_code == 200
    assert status_response.json()["status"] == "SUBMITTED"


def test_submit_application_unknown_customer_returns_404(client):
    test_client, _, product_id = client
    response = test_client.post(
        "/card-applications", json={"customer_id": 99999, "card_product_id": product_id}
    )
    assert response.status_code == 404


def test_submit_duplicate_application_returns_409(client):
    test_client, customer_id, product_id = client
    test_client.post("/card-applications", json={"customer_id": customer_id, "card_product_id": product_id})

    response = test_client.post(
        "/card-applications", json={"customer_id": customer_id, "card_product_id": product_id}
    )
    assert response.status_code == 409


def test_get_unknown_application_returns_404(client):
    test_client, _, _ = client
    response = test_client.get("/card-applications/99999")
    assert response.status_code == 404
