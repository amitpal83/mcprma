"""Builds and sends the card-offer email: plain text, one PDF attachment, SMTP.

Deliberately simple -- no templates, no drafts. send_card_offer() builds the
message and sends it immediately.

Config is read from the environment at send time:
  SMTP_HOST, MAIL_FROM            required
  SMTP_PORT                       default 587 (STARTTLS)
  SMTP_USER, SMTP_PASSWORD        optional; login only when SMTP_USER is set
  MAIL_REDIRECT_TO                optional; if set, every email goes to this
                                  address instead of the customer's (for testing)
"""
from __future__ import annotations

import logging
import os
import smtplib
from decimal import Decimal
from email.message import EmailMessage
from pathlib import Path

from api.schemas import CardProductOut

logger = logging.getLogger(__name__)

BROCHURE_PATH = Path(__file__).resolve().parent.parent / "brochure" / "card.pdf"


class OfferEmailError(Exception):
    """Anything that stops the offer email being built or sent."""


def _inr(amount: Decimal) -> str:
    return f"INR {amount:,.2f}"


def find_discount(product: CardProductOut, tier: int | None) -> str | None:
    """The product's discount text for this relationship tier, e.g.
    "25% off joining fees", or None if the product has none for that tier."""
    if tier is None:
        return None
    for discount in product.relationship_discounts_appl:
        if discount.relationship_tier == tier:
            return f"{discount.value} off {', '.join(discount.discount_type)}"
    return None


def build_message(
    customer_name: str,
    tier: int | None,
    recipient: str,
    product: CardProductOut,
    personal_note: str | None = None,
) -> EmailMessage:
    lines = [f"Dear {customer_name},", ""]
    if personal_note:
        lines += [personal_note.strip(), ""]

    lines.append(
        f"We would like to introduce the {product.name}, a {product.network} {product.card_type} card "
        "that we think suits you well."
    )

    if product.key_features:
        lines += ["", "Key benefits:"] + [f"  - {feature}" for feature in product.key_features]
    perks = []
    if product.lounge_visits_domestic_per_year:
        perks.append(f"{product.lounge_visits_domestic_per_year} domestic airport lounge visits a year")
    if product.lounge_visits_international_per_year:
        perks.append(f"{product.lounge_visits_international_per_year} international airport lounge visits a year")
    if product.reward_transfer_partners:
        perks.append("Reward points transfer to " + ", ".join(product.reward_transfer_partners))
    if perks:
        lines += ["", "Also included:"] + [f"  - {perk}" for perk in perks]

    lines += [
        "",
        "Fees:",
        f"  - Joining fee: {_inr(product.joining_fee)}",
        f"  - Annual fee: {_inr(product.annual_fee)}",
        f"  - Forex markup: {product.forex_markup_pct}%",
    ]

    discount = find_discount(product, tier)
    if discount:
        lines += ["", f"Special offer for you: as a valued customer you get {discount}."]

    if product.eligibility_criteria:
        lines += ["", "Eligibility:"] + [f"  - {criterion}" for criterion in product.eligibility_criteria]

    lines += [
        "",
        "The product brochure is attached. Please reply to this email or contact your "
        "Relationship Manager to apply or for any questions.",
        "",
        "Warm regards,",
        "Your Relationship Manager",
    ]

    message = EmailMessage()
    message["To"] = recipient
    message["Subject"] = f"{product.name}: benefits for you"
    message.set_content("\n".join(lines))
    return message


def _attach_brochure(message: EmailMessage) -> str:
    try:
        data = BROCHURE_PATH.read_bytes()
    except OSError as exc:
        raise OfferEmailError(f"Product brochure not available: {BROCHURE_PATH.name}") from exc
    message.add_attachment(data, maintype="application", subtype="pdf", filename=BROCHURE_PATH.name)
    return BROCHURE_PATH.name


def send_message(message: EmailMessage) -> str:
    """Send over SMTP (STARTTLS) and return the address it was delivered to."""
    host = os.environ.get("SMTP_HOST")
    sender = os.environ.get("MAIL_FROM")
    if not host or not sender:
        raise OfferEmailError("Email is not configured: set SMTP_HOST and MAIL_FROM")

    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASSWORD", "")
    redirect_to = os.environ.get("MAIL_REDIRECT_TO")

    message["From"] = sender
    if redirect_to:
        message.replace_header("To", redirect_to)
    delivered_to = message["To"]

    try:
        with smtplib.SMTP(host, port, timeout=30) as smtp:
            smtp.starttls()
            if user:
                smtp.login(user, password)
            smtp.send_message(message)
    except (smtplib.SMTPException, OSError) as exc:
        logger.error("Offer email send failed: %s", exc)
        raise OfferEmailError(f"Could not send the email: {exc}") from exc
    return delivered_to


def send_card_offer(
    customer_name: str,
    tier: int | None,
    recipient: str,
    product: CardProductOut,
    personal_note: str | None = None,
) -> dict:
    message = build_message(customer_name, tier, recipient, product, personal_note)
    attachment = _attach_brochure(message)
    subject = message["Subject"]
    sent_to = send_message(message)
    logger.info("Offer email sent: product_id=%s tier=%s", product.id, tier)
    return {
        "status": "SENT",
        "sent_to": sent_to,
        "subject": subject,
        "card_product_id": product.id,
        "attachment": attachment,
        "discount_applied": find_discount(product, tier),
    }
