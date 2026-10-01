"""Tests for the card recommendation HTTP endpoint (Step 7)."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.dependencies import get_db
from api.main import app
from db.models import Account, Base, Card, CardProduct, Customer, Transaction

ACCOUNT_NUMBER = "8552"


@pytest.fixture
def client(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'api_recs_test.db'}")
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
        debit_product = CardProduct(name="HDFC Debit Card", network="Visa", card_type="debit", forex_markup_pct=3.5)
        zero_forex_product = CardProduct(
            name="Global Elite Zero Forex Card",
            network="Visa",
            card_type="credit",
            forex_markup_pct=0,
            joining_fee=Decimal("15000.00"),
            relationship_discount_pct=Decimal("25.00"),
            min_relationship_tier_for_discount="PRIORITY",
        )
        session.add_all([debit_product, zero_forex_product])
        session.flush()

        card = Card(
            account_number=ACCOUNT_NUMBER,
            card_product_id=debit_product.id,
            last4="4821",
            network="Visa",
            card_type="debit",
        )
        session.add(card)
        session.flush()

        session.add(
            Transaction(
                account_number=ACCOUNT_NUMBER,
                card_id=card.id,
                txn_date=date.today(),
                value_date=date.today(),
                narration="WISDOM PROPERTY NL II",
                withdrawal_amount=Decimal("32000.00"),
                closing_balance=Decimal("100000.00"),
                txn_currency="EUR",
                txn_amount=Decimal("353.00"),
                forex_markup_amount=Decimal("1120.00"),
                gst_on_markup=Decimal("201.60"),
                category="Travel",
            )
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


def test_get_card_recommendation(client):
    test_client, card_id = client
    response = test_client.get(f"/cards/{card_id}/recommendation")

    assert response.status_code == 200
    body = response.json()
    assert body["recommended_product"]["name"] == "Global Elite Zero Forex Card"
    assert body["discount_pct_applied"] == "25.00"


def test_get_card_recommendation_unknown_card_returns_404(client):
    test_client, _ = client
    response = test_client.get("/cards/99999/recommendation")
    assert response.status_code == 404
