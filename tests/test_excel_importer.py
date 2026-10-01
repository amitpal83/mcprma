"""Tests for etl.excel_importer.

Each test builds its own throwaway SQLite file under pytest's tmp_path
fixture, so tests never touch the real data/rma.db.
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db.models import Base, Transaction
from etl.excel_importer import import_statement

EXCEL_PATH = Path(__file__).resolve().parent.parent / "data" / "Account_Statement_Sep26.xls"
ACCOUNT_NUMBER = "8552"


@pytest.fixture
def session_factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def test_import_first_20_transactions(session_factory):
    result = import_statement(EXCEL_PATH, ACCOUNT_NUMBER, max_rows=20, session_factory=session_factory)

    assert result.inserted == 20
    assert result.skipped == 0
    assert result.errors == []

    with session_factory() as session:
        rows = (
            session.query(Transaction)
            .filter_by(account_number=ACCOUNT_NUMBER)
            .order_by(Transaction.id)
            .all()
        )
        assert len(rows) == 20

        first = rows[0]
        assert first.withdrawal_amount == Decimal("20000.00")
        assert first.deposit_amount is None
        assert first.closing_balance == Decimal("1670150.61")
        assert "PREETIKA PAL" in first.narration


def test_reimport_is_idempotent(session_factory):
    import_statement(EXCEL_PATH, ACCOUNT_NUMBER, max_rows=20, session_factory=session_factory)
    result = import_statement(EXCEL_PATH, ACCOUNT_NUMBER, max_rows=20, session_factory=session_factory)

    assert result.inserted == 0
    assert result.skipped == 20

    with session_factory() as session:
        count = session.query(Transaction).filter_by(account_number=ACCOUNT_NUMBER).count()
        assert count == 20


def test_missing_file_raises(session_factory):
    with pytest.raises(FileNotFoundError):
        import_statement(Path("does_not_exist.xls"), ACCOUNT_NUMBER, session_factory=session_factory)
