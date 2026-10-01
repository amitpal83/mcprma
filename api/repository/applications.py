"""Card application lifecycle.

Fee/discount computation reuses the same relationship-tier gating and
GST-on-fee fallback as api.repository.cards.recommend_card_upgrade, so a
customer applying for the card sees the same numbers the RM quoted them.
"""
from __future__ import annotations

import logging
from decimal import Decimal

from sqlalchemy.orm import Session

from api.repository.cards import DEFAULT_GST_RATE, _tier_meets_minimum, get_card_product
from api.repository.customers import CustomerNotFoundError
from db.models import CardApplication, Customer

logger = logging.getLogger(__name__)


class DuplicateApplicationError(Exception):
    def __init__(self, customer_id: int, card_product_id: int) -> None:
        self.customer_id = customer_id
        self.card_product_id = card_product_id
        super().__init__(
            f"A submitted application already exists for customer={customer_id}, "
            f"card_product={card_product_id}"
        )


class CardApplicationNotFoundError(Exception):
    def __init__(self, application_id: int) -> None:
        self.application_id = application_id
        super().__init__(f"Card application not found: {application_id}")


def create_card_application(
    session: Session,
    customer_id: int,
    card_product_id: int,
    delivery_address: str | None = None,
) -> CardApplication:
    """Submit a card application, computing any relationship-tier discount.

    Commits internally (write function).

    Raises:
        CustomerNotFoundError: if customer_id doesn't exist.
        CardProductNotFoundError: if card_product_id doesn't exist.
        DuplicateApplicationError: if a SUBMITTED application already exists
            for this (customer_id, card_product_id) pair. A new application
            is allowed once a prior one moves to REJECTED/CANCELLED.
    """
    customer = session.get(Customer, customer_id)
    if customer is None:
        raise CustomerNotFoundError(customer_id)

    product = get_card_product(session, card_product_id)  # raises CardProductNotFoundError

    existing = (
        session.query(CardApplication)
        .filter(
            CardApplication.customer_id == customer_id,
            CardApplication.card_product_id == card_product_id,
            CardApplication.status == "SUBMITTED",
        )
        .first()
    )
    if existing is not None:
        raise DuplicateApplicationError(customer_id, card_product_id)

    discount_pct_applied = None
    fee = product.joining_fee
    if product.relationship_discount_pct is not None and _tier_meets_minimum(
        customer.relationship_tier, product.min_relationship_tier_for_discount
    ):
        discount_pct_applied = product.relationship_discount_pct
        fee = product.joining_fee * (Decimal("1") - discount_pct_applied / Decimal("100"))

    fee_charged = (fee * (Decimal("1") + DEFAULT_GST_RATE)).quantize(Decimal("0.01"))

    application = CardApplication(
        customer_id=customer_id,
        card_product_id=card_product_id,
        discount_pct_applied=discount_pct_applied,
        fee_charged=fee_charged,
        delivery_address=delivery_address,
    )
    session.add(application)
    session.commit()
    logger.info(
        "create_card_application: customer=%s product=%s -> application_id=%s fee_charged=%s",
        customer_id,
        card_product_id,
        application.id,
        fee_charged,
    )
    return application


def get_card_application_status(session: Session, application_id: int) -> CardApplication:
    """Raises CardApplicationNotFoundError if application_id doesn't exist."""
    application = session.get(CardApplication, application_id)
    if application is None:
        raise CardApplicationNotFoundError(application_id)
    return application
