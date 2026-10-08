"""Core query logic for account transaction lookups.

"""
from __future__ import annotations

import logging
from datetime import date

from sqlalchemy.orm import Session

from db.models import Account, Transaction

logger = logging.getLogger(__name__)


class AccountNotFoundError(Exception):
    def __init__(self, account_number: str) -> None:
        self.account_number = account_number
        super().__init__(f"Account not found: {account_number}")


class InvalidDateRangeError(Exception):
    def __init__(self, from_date: date, to_date: date) -> None:
        self.from_date = from_date
        self.to_date = to_date
        super().__init__(f"from_date ({from_date}) must not be after to_date ({to_date})")


def get_account_txn_details(
    session: Session,
    account_number: str,
    from_date: date,
    to_date: date,
) -> list[Transaction]:
    """Return transactions for account_number with txn_date in [from_date, to_date].

    Raises:
        AccountNotFoundError: if account_number has no matching account row.
        InvalidDateRangeError: if from_date is after to_date.
    """
    if from_date > to_date:
        raise InvalidDateRangeError(from_date, to_date)

    account = session.get(Account, account_number)
    if account is None:
        raise AccountNotFoundError(account_number)

    transactions = (
        session.query(Transaction)
        .filter(
            Transaction.account_number == account_number,
            Transaction.txn_date >= from_date,
            Transaction.txn_date <= to_date,
        )
        .order_by(Transaction.txn_date, Transaction.id)
        .all()
    )

    logger.info(
        "get_account_txn_details: account=%s from=%s to=%s -> %s rows",
        account_number,
        from_date,
        to_date,
        len(transactions),
    )
    return transactions
