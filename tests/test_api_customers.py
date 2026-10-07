"""Tests for the customer profile / delivery-preference HTTP endpoints (Step 5)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.dependencies import get_db
from api.main import app
from db.models import Account, Base, Customer

ACCOUNT_NUMBER = "8552"


@pytest.fixture
def client(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'api_customers_test.db'}")
    Base.metadata.create_all(bind=engine)
    TestSessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

    with TestSessionLocal() as session:
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
        customer_id = session.query(Customer).filter_by(account_number=ACCOUNT_NUMBER).first().id

    def override_get_db():
        session = TestSessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield TestClient(app), customer_id
    finally:
        app.dependency_overrides.clear()


def test_get_account_customer_masks_email(client):
    test_client, _ = client
    response = test_client.get(f"/accounts/{ACCOUNT_NUMBER}/customer")

    assert response.status_code == 200
    body = response.json()
    assert body["full_name"] == "Mr. Mehta"
    assert body["registered_email"] == "rammehta@gmail.com"
    assert "registered_email_masked" not in body


def test_get_account_customer_unknown_account_returns_404(client):
    test_client, _ = client
    response = test_client.get("/accounts/does-not-exist/customer")
    assert response.status_code == 404


def test_update_delivery_preference(client):
    test_client, customer_id = client
    response = test_client.patch(
        f"/customers/{customer_id}/delivery-preference",
        json={"preferred_delivery_address_type": "OFFICE"},
    )

    assert response.status_code == 200
    assert response.json()["preferred_delivery_address_type"] == "OFFICE"


def test_update_delivery_preference_invalid_value_returns_400(client):
    test_client, customer_id = client
    response = test_client.patch(
        f"/customers/{customer_id}/delivery-preference",
        json={"preferred_delivery_address_type": "WAREHOUSE"},
    )
    assert response.status_code == 400


def test_update_delivery_preference_unknown_customer_returns_404(client):
    test_client, _ = client
    response = test_client.patch(
        "/customers/99999/delivery-preference",
        json={"preferred_delivery_address_type": "OFFICE"},
    )
    assert response.status_code == 404
