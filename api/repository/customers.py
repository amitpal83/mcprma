"""Customer profile lookups and delivery-preference updates.

PII masking (customer email) deliberately does NOT live here -- this module
returns the full ORM row, unmasked, so any future internal-only consumer can
still read the real email. Masking happens at the schema layer
(api/schemas.py CustomerOut), which is the one place that decides what a
caller-facing response looks like.
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from db.models import Customer

logger = logging.getLogger(__name__)

VALID_DELIVERY_ADDRESS_TYPES = {"OFFICE", "HOME"}


class CustomerNotFoundError(Exception):
    def __init__(self, identifier: str | int) -> None:
        self.identifier = identifier
        super().__init__(f"Customer not found: {identifier}")


class InvalidDeliveryAddressTypeError(Exception):
    def __init__(self, value: str) -> None:
        self.value = value
        super().__init__(
            f"preferred_delivery_address_type must be one of "
            f"{sorted(VALID_DELIVERY_ADDRESS_TYPES)}, got: {value!r}"
        )


def mask_email(email: str) -> str:
    """Mask the local part of an email, keeping the domain visible.

    E.g. "rammehta@outlook.com" -> "ram******@outlook.com" -- matches how
    the RM Twin demo script shows a masked email being read back to the
    customer in real time (only the local part is obscured, the domain
    stays visible).
    """
    local, _, domain = email.partition("@")
    prefix_len = min(3, len(local))
    prefix = local[:prefix_len]
    stars = max(len(local) - prefix_len, 3)
    return f"{prefix}{'*' * stars}@{domain}"


def get_customer_by_account(session: Session, account_number: str) -> Customer:
    """Raises CustomerNotFoundError if no customer profile exists for this account."""
    customer = session.query(Customer).filter(Customer.account_number == account_number).first()
    if customer is None:
        raise CustomerNotFoundError(account_number)
    return customer


def update_customer_delivery_preference(
    session: Session, customer_id: int, preferred_delivery_address_type: str
) -> Customer:
    """Update which address a customer prefers for card/document delivery.

    Commits internally (this is a write function, unlike the read-only
    lookups elsewhere in this package).

    Raises:
        InvalidDeliveryAddressTypeError: if the value isn't "OFFICE"/"HOME".
        CustomerNotFoundError: if customer_id doesn't exist.
    """
    if preferred_delivery_address_type not in VALID_DELIVERY_ADDRESS_TYPES:
        raise InvalidDeliveryAddressTypeError(preferred_delivery_address_type)

    customer = session.get(Customer, customer_id)
    if customer is None:
        raise CustomerNotFoundError(customer_id)

    customer.preferred_delivery_address_type = preferred_delivery_address_type
    session.commit()
    logger.info(
        "update_customer_delivery_preference: customer_id=%s -> %s",
        customer_id,
        preferred_delivery_address_type,
    )
    return customer
