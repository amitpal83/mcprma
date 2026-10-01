"""Tests for the getAccountTxnDetails HTTP endpoint.

Uses FastAPI's TestClient against a throwaway SQLite DB seeded directly via
the ORM (not through the Excel importer, to keep this suite independent of
the source file).
"""
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
    engine = create_engine(f"sqlite:///{tmp_path / 'api_test.db'}")
    Base.metadata.create_all(bind=engine)
    TestSessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

    with TestSessionLocal() as session:
        session.add(Account(account_number=ACCOUNT_NUMBER))
        session.add_all(
            [
                Transaction(
                    account_number=ACCOUNT_NUMBER,
                    txn_date=date(2026, 9, 1),
                    value_date=date(2026, 9, 1),
                    narration="UPI-EARLY-TXN",
                    reference_no="REF001",
                    withdrawal_amount=Decimal("100.00"),
                    deposit_amount=None,
                    closing_balance=Decimal("900.00"),
                ),
                Transaction(
                    account_number=ACCOUNT_NUMBER,
                    txn_date=date(2026, 9, 15),
                    value_date=date(2026, 9, 15),
                    narration="UPI-MID-TXN",
                    reference_no="REF002",
                    withdrawal_amount=None,
                    deposit_amount=Decimal("500.00"),
                    closing_balance=Decimal("1400.00"),
                ),
                Transaction(
                    account_number=ACCOUNT_NUMBER,
                    txn_date=date(2026, 9, 30),
                    value_date=date(2026, 9, 30),
                    narration="UPI-LATE-TXN",
                    reference_no="REF003",
                    withdrawal_amount=Decimal("50.00"),
                    deposit_amount=None,
                    closing_balance=Decimal("1350.00"),
                ),
            ]
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


def test_returns_transactions_within_range(client):
    response = client.get(
        f"/accounts/{ACCOUNT_NUMBER}/transactions",
        params={"from_date": "2026-09-01", "to_date": "2026-09-20"},
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 2
    assert [t["reference_no"] for t in body] == ["REF001", "REF002"]


def test_full_range_returns_all_transactions(client):
    response = client.get(
        f"/accounts/{ACCOUNT_NUMBER}/transactions",
        params={"from_date": "2026-09-01", "to_date": "2026-09-30"},
    )

    assert response.status_code == 200
    assert len(response.json()) == 3


def test_unknown_account_returns_404(client):
    response = client.get(
        "/accounts/does-not-exist/transactions",
        params={"from_date": "2026-09-01", "to_date": "2026-09-30"},
    )

    assert response.status_code == 404


def test_invalid_date_range_returns_400(client):
    response = client.get(
        f"/accounts/{ACCOUNT_NUMBER}/transactions",
        params={"from_date": "2026-09-30", "to_date": "2026-09-01"},
    )

    assert response.status_code == 400
