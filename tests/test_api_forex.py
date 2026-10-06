"""Tests for the card-transaction search / forex-summary / category-breakdown
HTTP endpoints (Step 4)."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.dependencies import get_db
from api.main import app
from db.models import Account, Base, Card, CardProduct, Merchant, MerchantAlias, Transaction

ACCOUNT_NUMBER = "8552"
AS_OF = "2026-09-30"


@pytest.fixture
def client(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'api_forex_test.db'}")
    Base.metadata.create_all(bind=engine)
    TestSessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

    with TestSessionLocal() as session:
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
                ),
            ]
        )
        session.commit()
        session.refresh(card)
        card_id = card.id

    def override_get_db():
        session = TestSessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield TestClient(app), card_id
    finally:
        app.dependency_overrides.clear()


def test_search_card_transactions_by_merchant_text(client):
    test_client, card_id = client
    response = test_client.get(
        f"/cards/{card_id}/transactions",
        params={"from_date": "2026-08-01", "to_date": "2026-08-31", "merchant_text": "Wisdom Property"},
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["reference_no"] == "SEED-FX-001"


def test_search_card_transactions_unknown_card_returns_404(client):
    test_client, _ = client
    response = test_client.get(
        "/cards/99999/transactions", params={"from_date": "2026-01-01", "to_date": "2026-12-31"}
    )
    assert response.status_code == 404


def test_search_card_transactions_invalid_range_returns_400(client):
    test_client, card_id = client
    response = test_client.get(
        f"/cards/{card_id}/transactions", params={"from_date": "2026-12-31", "to_date": "2026-01-01"}
    )
    assert response.status_code == 400


def test_search_card_transactions_invalid_amount_range_returns_400(client):
    test_client, card_id = client
    response = test_client.get(
        f"/cards/{card_id}/transactions",
        params={
            "from_date": "2026-01-01",
            "to_date": "2026-12-31",
            "amount_min": "39000",
            "amount_max": "21000",
        },
    )
    assert response.status_code == 400


def test_forex_summary(client):
    test_client, card_id = client
    response = test_client.get(f"/cards/{card_id}/forex-summary", params={"as_of_date": AS_OF})
    assert response.status_code == 200
    body = response.json()
    assert body["transaction_count"] == 1
    assert body["total_forex_spend_inr"] == "32000.00"


def test_forex_summary_unknown_card_returns_404(client):
    test_client, _ = client
    response = test_client.get("/cards/99999/forex-summary")
    assert response.status_code == 404


def test_category_breakdown(client):
    test_client, card_id = client
    response = test_client.get(
        f"/cards/{card_id}/category-breakdown", params={"from_date": "2026-01-01", "to_date": "2026-12-31"}
    )
    assert response.status_code == 200
    categories = {row["category"] for row in response.json()}
    assert categories == {"Travel", "Groceries"}
