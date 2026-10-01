"""Tests for the card & card-product catalogue HTTP endpoints (Step 3)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.dependencies import get_db
from api.main import app
from api.repository.cards import encode_reward_transfer_partners
from db.models import Account, Base, Card, CardProduct

ACCOUNT_NUMBER = "8552"


@pytest.fixture
def client(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'api_cards_test.db'}")
    Base.metadata.create_all(bind=engine)
    TestSessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

    with TestSessionLocal() as session:
        session.add(Account(account_number=ACCOUNT_NUMBER))

        debit_product = CardProduct(
            name="HDFC Debit Card", network="Visa", card_type="debit", forex_markup_pct=3.5
        )
        credit_product = CardProduct(
            name="Global Elite Zero Forex Card",
            network="Visa",
            card_type="credit",
            forex_markup_pct=0,
            guest_visits_per_year=12,
            reward_transfer_partners=encode_reward_transfer_partners(["Flying Blue"]),
        )
        session.add_all([debit_product, credit_product])
        session.flush()

        session.add(
            Card(
                account_number=ACCOUNT_NUMBER,
                card_product_id=debit_product.id,
                last4="4821",
                network="Visa",
                card_type="debit",
            )
        )
        session.commit()
        session.refresh(debit_product)
        session.refresh(credit_product)
        debit_product_id = debit_product.id
        credit_product_id = credit_product.id

    def override_get_db():
        session = TestSessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield TestClient(app), debit_product_id, credit_product_id
    finally:
        app.dependency_overrides.clear()


def test_list_account_cards(client):
    test_client, _, _ = client
    response = test_client.get(f"/accounts/{ACCOUNT_NUMBER}/cards")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["last4"] == "4821"


def test_list_cards_unknown_account_returns_404(client):
    test_client, _, _ = client
    response = test_client.get("/accounts/does-not-exist/cards")
    assert response.status_code == 404


def test_get_card(client):
    test_client, _, _ = client
    card_id = test_client.get(f"/accounts/{ACCOUNT_NUMBER}/cards").json()[0]["id"]

    response = test_client.get(f"/cards/{card_id}")
    assert response.status_code == 200
    assert response.json()["last4"] == "4821"


def test_get_unknown_card_returns_404(client):
    test_client, _, _ = client
    response = test_client.get("/cards/99999")
    assert response.status_code == 404


def test_list_card_products(client):
    test_client, _, _ = client
    response = test_client.get("/card-products")

    assert response.status_code == 200
    names = {p["name"] for p in response.json()}
    assert names == {"HDFC Debit Card", "Global Elite Zero Forex Card"}


def test_list_card_products_filters_by_type(client):
    test_client, _, _ = client
    response = test_client.get("/card-products", params={"card_type": "credit"})

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["name"] == "Global Elite Zero Forex Card"
    assert body[0]["reward_transfer_partners"] == ["Flying Blue"]


def test_get_card_product(client):
    test_client, _, credit_product_id = client
    response = test_client.get(f"/card-products/{credit_product_id}")

    assert response.status_code == 200
    assert response.json()["name"] == "Global Elite Zero Forex Card"


def test_get_unknown_card_product_returns_404(client):
    test_client, _, _ = client
    response = test_client.get("/card-products/99999")
    assert response.status_code == 404
