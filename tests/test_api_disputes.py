"""Tests for the dispute HTTP endpoints (Step 6)."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.dependencies import get_db
from api.main import app
from db.models import Account, Base, Transaction

ACCOUNT_NUMBER = "8552"


@pytest.fixture
def client(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'api_disputes_test.db'}")
    Base.metadata.create_all(bind=engine)
    TestSessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

    with TestSessionLocal() as session:
        session.add(Account(account_number=ACCOUNT_NUMBER))
        session.add(
            Transaction(
                txn_currency="INR",
                account_number=ACCOUNT_NUMBER,
                txn_date=date(2026, 8, 12),
                merchant="WISDOM PROPERTY NL II",
                txn_amount_INR=Decimal("32000.00"),
            )
        )
        session.commit()
        transaction_id = session.query(Transaction).filter_by(account_number=ACCOUNT_NUMBER).first().id

    def override_get_db():
        session = TestSessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield TestClient(app), transaction_id
    finally:
        app.dependency_overrides.clear()


def test_raise_and_withdraw_dispute_lifecycle(client):
    test_client, transaction_id = client

    create_response = test_client.post(
        f"/transactions/{transaction_id}/disputes", json={"reason": "Unrecognized charge"}
    )
    assert create_response.status_code == 201
    dispute = create_response.json()
    assert dispute["status"] == "OPEN"

    withdraw_response = test_client.post(f"/disputes/{dispute['id']}/withdraw")
    assert withdraw_response.status_code == 200
    assert withdraw_response.json()["status"] == "WITHDRAWN"


def test_raise_dispute_unknown_transaction_returns_404(client):
    test_client, _ = client
    response = test_client.post("/transactions/99999/disputes", json={"reason": "Unrecognized charge"})
    assert response.status_code == 404


def test_raise_dispute_when_already_open_returns_409(client):
    test_client, transaction_id = client
    test_client.post(f"/transactions/{transaction_id}/disputes", json={"reason": "Unrecognized charge"})

    response = test_client.post(f"/transactions/{transaction_id}/disputes", json={"reason": "Still not mine"})
    assert response.status_code == 409


def test_withdraw_unknown_dispute_returns_404(client):
    test_client, _ = client
    response = test_client.post("/disputes/99999/withdraw")
    assert response.status_code == 404


def test_withdraw_already_withdrawn_dispute_returns_409(client):
    test_client, transaction_id = client
    dispute = test_client.post(
        f"/transactions/{transaction_id}/disputes", json={"reason": "Unrecognized charge"}
    ).json()
    test_client.post(f"/disputes/{dispute['id']}/withdraw")

    response = test_client.post(f"/disputes/{dispute['id']}/withdraw")
    assert response.status_code == 409
