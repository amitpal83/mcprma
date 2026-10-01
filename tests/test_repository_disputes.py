"""Tests for api.repository.disputes (Step 6)."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.repository.disputes import (
    DisputeAlreadyOpenError,
    DisputeNotFoundError,
    DisputeNotOpenError,
    TransactionNotFoundError,
    create_dispute,
    withdraw_dispute,
)
from db.models import Account, Base, Transaction

ACCOUNT_NUMBER = "8552"


@pytest.fixture
def session_factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def seeded_session_factory(session_factory):
    with session_factory() as session:
        session.add(Account(account_number=ACCOUNT_NUMBER))
        session.add(
            Transaction(
                account_number=ACCOUNT_NUMBER,
                txn_date=date(2026, 8, 12),
                value_date=date(2026, 8, 12),
                narration="WISDOM PROPERTY NL II",
                withdrawal_amount=Decimal("32000.00"),
                closing_balance=Decimal("100000.00"),
            )
        )
        session.commit()
    return session_factory


def _seeded_transaction_id(factory) -> int:
    with factory() as session:
        return session.query(Transaction).filter_by(account_number=ACCOUNT_NUMBER).first().id


def test_create_dispute(seeded_session_factory):
    transaction_id = _seeded_transaction_id(seeded_session_factory)
    with seeded_session_factory() as session:
        dispute = create_dispute(session, transaction_id, "Unrecognized charge")
        assert dispute.status == "OPEN"
        assert dispute.reason == "Unrecognized charge"


def test_create_dispute_unknown_transaction_raises(seeded_session_factory):
    with seeded_session_factory() as session:
        with pytest.raises(TransactionNotFoundError):
            create_dispute(session, 99999, "Unrecognized charge")


def test_create_dispute_when_already_open_raises(seeded_session_factory):
    transaction_id = _seeded_transaction_id(seeded_session_factory)
    with seeded_session_factory() as session:
        create_dispute(session, transaction_id, "Unrecognized charge")
        with pytest.raises(DisputeAlreadyOpenError):
            create_dispute(session, transaction_id, "Still not mine")


def test_withdraw_dispute_lifecycle(seeded_session_factory):
    transaction_id = _seeded_transaction_id(seeded_session_factory)
    with seeded_session_factory() as session:
        dispute_id = create_dispute(session, transaction_id, "Unrecognized charge").id

    with seeded_session_factory() as session:
        withdrawn = withdraw_dispute(session, dispute_id)
        assert withdrawn.status == "WITHDRAWN"
        assert withdrawn.resolved_at is not None


def test_withdraw_unknown_dispute_raises(seeded_session_factory):
    with seeded_session_factory() as session:
        with pytest.raises(DisputeNotFoundError):
            withdraw_dispute(session, 99999)


def test_withdraw_already_withdrawn_dispute_raises(seeded_session_factory):
    transaction_id = _seeded_transaction_id(seeded_session_factory)
    with seeded_session_factory() as session:
        dispute_id = create_dispute(session, transaction_id, "Unrecognized charge").id
        withdraw_dispute(session, dispute_id)

        with pytest.raises(DisputeNotOpenError):
            withdraw_dispute(session, dispute_id)


def test_second_dispute_allowed_after_first_is_withdrawn(seeded_session_factory):
    transaction_id = _seeded_transaction_id(seeded_session_factory)
    with seeded_session_factory() as session:
        first_id = create_dispute(session, transaction_id, "Unrecognized charge").id
        withdraw_dispute(session, first_id)

        second = create_dispute(session, transaction_id, "Raised again")
        assert second.status == "OPEN"
        assert second.id != first_id
