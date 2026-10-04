"""Customer-360 snapshot lookups.

Framework-agnostic, following the same pattern as api/repository/customers.py.
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from db.models import Customer360

logger = logging.getLogger(__name__)


class Customer360NotFoundError(Exception):
    def __init__(self, account_number: str) -> None:
        self.account_number = account_number
        super().__init__(f"No customer-360 snapshot found for account: {account_number}")


def get_customer_360(session: Session, account_number: str) -> Customer360:
    """Raises Customer360NotFoundError if no snapshot exists for account_number."""
    snapshot = session.query(Customer360).filter(Customer360.account_number == account_number).first()
    if snapshot is None:
        raise Customer360NotFoundError(account_number)

    logger.info("get_customer_360: account=%s -> customer_id=%s", account_number, snapshot.customer_id)
    return snapshot
