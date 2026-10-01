"""Dispute lifecycle.

Scope matches the demo: a customer raises a dispute, then withdraws it once
they recognize the charge. No separate bank-side resolution state is needed.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from db.models import Dispute, Transaction

logger = logging.getLogger(__name__)


class TransactionNotFoundError(Exception):
    def __init__(self, transaction_id: int) -> None:
        self.transaction_id = transaction_id
        super().__init__(f"Transaction not found: {transaction_id}")


class DisputeAlreadyOpenError(Exception):
    def __init__(self, transaction_id: int) -> None:
        self.transaction_id = transaction_id
        super().__init__(f"An open dispute already exists for transaction: {transaction_id}")


class DisputeNotFoundError(Exception):
    def __init__(self, dispute_id: int) -> None:
        self.dispute_id = dispute_id
        super().__init__(f"Dispute not found: {dispute_id}")


class DisputeNotOpenError(Exception):
    def __init__(self, dispute_id: int, status: str) -> None:
        self.dispute_id = dispute_id
        self.status = status
        super().__init__(f"Dispute {dispute_id} is not open (status={status})")


def create_dispute(session: Session, transaction_id: int, reason: str) -> Dispute:
    """Raise a dispute against a transaction. Commits internally (write function).

    Raises:
        TransactionNotFoundError: if transaction_id doesn't exist.
        DisputeAlreadyOpenError: if an OPEN dispute already exists for this transaction.
    """
    transaction = session.get(Transaction, transaction_id)
    if transaction is None:
        raise TransactionNotFoundError(transaction_id)

    existing_open = (
        session.query(Dispute)
        .filter(Dispute.transaction_id == transaction_id, Dispute.status == "OPEN")
        .first()
    )
    if existing_open is not None:
        raise DisputeAlreadyOpenError(transaction_id)

    dispute = Dispute(transaction_id=transaction_id, reason=reason)
    session.add(dispute)
    session.commit()
    logger.info("create_dispute: transaction_id=%s -> dispute_id=%s", transaction_id, dispute.id)
    return dispute


def withdraw_dispute(session: Session, dispute_id: int) -> Dispute:
    """Withdraw an open dispute (e.g. the customer recognizes the charge).
    Commits internally (write function).

    Raises:
        DisputeNotFoundError: if dispute_id doesn't exist.
        DisputeNotOpenError: if the dispute isn't currently OPEN.
    """
    dispute = session.get(Dispute, dispute_id)
    if dispute is None:
        raise DisputeNotFoundError(dispute_id)
    if dispute.status != "OPEN":
        raise DisputeNotOpenError(dispute_id, dispute.status)

    dispute.status = "WITHDRAWN"
    dispute.resolved_at = datetime.now(timezone.utc)
    session.commit()
    logger.info("withdraw_dispute: dispute_id=%s -> WITHDRAWN", dispute_id)
    return dispute
