"""Card & card-product catalogue lookups.

Read-only for this step (Step 3) -- search/forex-summary/recommendation
logic is added on top of this module in later steps.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from api.repository.transactions import AccountNotFoundError, InvalidDateRangeError
from db.models import Account, Card, CardProduct, Transaction

logger = logging.getLogger(__name__)

# Fallback GST-on-markup rate, used only when a card has no forex history to
# derive an effective rate from (can't divide by a zero markup total).
DEFAULT_GST_RATE = Decimal("0.18")

# Ordinal ranking for "does this customer's tier meet the minimum required
# for a discount" -- a simple, demo-scoped hierarchy, not a general-purpose
# tier registry.
class CardNotFoundError(Exception):
    def __init__(self, card_id: int) -> None:
        self.card_id = card_id
        super().__init__(f"Card not found: {card_id}")


class CardProductNotFoundError(Exception):
    def __init__(self, card_product_id: int) -> None:
        self.card_product_id = card_product_id
        super().__init__(f"Card product not found: {card_product_id}")


class InvalidAmountRangeError(Exception):
    def __init__(self, amount_min: Decimal, amount_max: Decimal) -> None:
        self.amount_min = amount_min
        self.amount_max = amount_max
        super().__init__(f"amount_min ({amount_min}) must not be greater than amount_max ({amount_max})")


class InvalidTransactionTypeError(Exception):
    def __init__(self, transaction_type: str) -> None:
        self.transaction_type = transaction_type
        super().__init__(f'transaction_type must be "domestic" or "international", got: {transaction_type!r}')


def decode_reward_transfer_partners(raw: str | None) -> list[str]:
    """Decode CardProduct.reward_transfer_partners (JSON text) into a list.

    SQLite has no native JSON type, so this column is stored as
    json.dumps(list[str]); this is the one place that knows that encoding,
    so callers (schema layer, recommendation logic) never see raw JSON text.
    """
    if not raw:
        return []
    return json.loads(raw)


def encode_reward_transfer_partners(partners: list[str] | None) -> str | None:
    if not partners:
        return None
    return json.dumps(partners)


def decode_key_features(raw: str | None) -> list[str]:
    """Decode CardProduct.key_features (JSON text), same convention as
    decode_reward_transfer_partners."""
    if not raw:
        return []
    return json.loads(raw)


def encode_key_features(features: list[str] | None) -> str | None:
    if not features:
        return None
    return json.dumps(features)


def decode_eligibility_criteria(raw: str | None) -> list[str]:
    """Decode CardProduct.eligibility_criteria (JSON text), same convention as
    decode_reward_transfer_partners."""
    if not raw:
        return []
    return json.loads(raw)


def encode_eligibility_criteria(criteria: list[str] | None) -> str | None:
    if not criteria:
        return None
    return json.dumps(criteria)


def list_cards_for_account(session: Session, account_number: str) -> list[Card]:
    """Return all cards linked to account_number.

    Raises:
        AccountNotFoundError: if account_number has no matching account row.
    """
    account = session.get(Account, account_number)
    if account is None:
        raise AccountNotFoundError(account_number)

    cards = (
        session.query(Card)
        .filter(Card.account_number == account_number)
        .order_by(Card.id)
        .all()
    )
    logger.info("list_cards_for_account: account=%s -> %s rows", account_number, len(cards))
    return cards


def get_card(session: Session, card_id: int) -> Card:
    """Raises CardNotFoundError if card_id doesn't exist."""
    card = session.get(Card, card_id)
    if card is None:
        raise CardNotFoundError(card_id)
    return card


def list_card_products(
    session: Session,
    card_type: str | None = None,
    active_only: bool = True,
) -> list[CardProduct]:
    """List catalogue products, optionally filtered by card_type ('debit'/'credit')."""
    query = session.query(CardProduct)
    if card_type is not None:
        query = query.filter(CardProduct.card_type == card_type)
    if active_only:
        query = query.filter(CardProduct.is_active.is_(True))

    products = query.order_by(CardProduct.id).all()
    logger.info(
        "list_card_products: card_type=%s active_only=%s -> %s rows",
        card_type,
        active_only,
        len(products),
    )
    return products


def get_card_product(session: Session, card_product_id: int) -> CardProduct:
    """Raises CardProductNotFoundError if card_product_id doesn't exist."""
    product = session.get(CardProduct, card_product_id)
    if product is None:
        raise CardProductNotFoundError(card_product_id)
    return product


def search_card_transactions(
    session: Session,
    card_id: int,
    from_date: date,
    to_date: date,
    amount_min: Decimal | None = None,
    amount_max: Decimal | None = None,
    merchant_text: str | None = None,
) -> list[Transaction]:
    """Search a card's transactions by date range, optional amount range, and merchant text.

    amount_min/amount_max match against EITHER the original foreign-currency
    amount (txn_amount) OR the INR-settled amount (txn_amount_INR), since
    a caller searching "~350" doesn't know in advance which currency the
    transaction they're thinking of was in.

    merchant_text is a case-insensitive substring match against the raw
    merchant text (e.g. "Wisdom Property" matches "WISDOM PROPERTY NL II").

    Raises:
        CardNotFoundError: if card_id doesn't exist.
        InvalidDateRangeError: if from_date is after to_date.
        InvalidAmountRangeError: if amount_min is greater than amount_max.
    """
    get_card(session, card_id)
    if from_date > to_date:
        raise InvalidDateRangeError(from_date, to_date)
    if amount_min is not None and amount_max is not None and amount_min > amount_max:
        raise InvalidAmountRangeError(amount_min, amount_max)

    query = session.query(Transaction).filter(
        Transaction.card_id == card_id,
        Transaction.txn_date >= from_date,
        Transaction.txn_date <= to_date,
    )

    if amount_min is not None or amount_max is not None:
        conditions = []
        for column in (Transaction.txn_amount, Transaction.txn_amount_INR):
            condition = column.isnot(None)
            if amount_min is not None:
                condition = condition & (column >= amount_min)
            if amount_max is not None:
                condition = condition & (column <= amount_max)
            conditions.append(condition)
        query = query.filter(or_(*conditions))

    if merchant_text:
        query = query.filter(Transaction.merchant.ilike(f"%{merchant_text}%"))

    transactions = query.order_by(Transaction.txn_date, Transaction.id).all()
    logger.info(
        "search_card_transactions: card=%s from=%s to=%s amount=[%s,%s] merchant_text=%r -> %s rows",
        card_id,
        from_date,
        to_date,
        amount_min,
        amount_max,
        merchant_text,
        len(transactions),
    )
    return transactions


@dataclass
class ForexSummary:
    card_id: int
    from_date: date
    to_date: date
    total_forex_spend_inr: Decimal
    total_markup_amount: Decimal
    transaction_count: int


def get_card_forex_summary(session: Session, card_id: int, as_of_date: date | None = None) -> ForexSummary:
    """Summarize a card's forex spend over the trailing 365 days ending as_of_date.

    Raises:
        CardNotFoundError: if card_id doesn't exist.
    """
    get_card(session, card_id)
    as_of = as_of_date or date.today()
    window_start = as_of - timedelta(days=365)

    rows = (
        session.query(Transaction)
        .filter(
            Transaction.card_id == card_id,
            Transaction.txn_currency != "INR",
            Transaction.txn_date >= window_start,
            Transaction.txn_date <= as_of,
        )
        .all()
    )

    total_spend = sum((row.txn_amount_INR or Decimal("0") for row in rows), Decimal("0"))
    total_markup = sum((row.forex_markup_amount_INR or Decimal("0") for row in rows), Decimal("0"))

    logger.info(
        "get_card_forex_summary: card=%s window=[%s,%s] -> spend=%s markup=%s (%s txns)",
        card_id,
        window_start,
        as_of,
        total_spend,
        total_markup,
        len(rows),
    )
    return ForexSummary(
        card_id=card_id,
        from_date=window_start,
        to_date=as_of,
        total_forex_spend_inr=total_spend,
        total_markup_amount=total_markup,
        transaction_count=len(rows),
    )


@dataclass
class CategorySpend:
    category: str | None
    total_amount: Decimal
    transaction_count: int


def get_card_category_breakdown(
    session: Session, card_id: int, from_date: date, to_date: date
) -> list[CategorySpend]:
    """Group a card's spend by category over a date range.

    Raises:
        CardNotFoundError: if card_id doesn't exist.
        InvalidDateRangeError: if from_date is after to_date.
    """
    get_card(session, card_id)
    if from_date > to_date:
        raise InvalidDateRangeError(from_date, to_date)

    rows = (
        session.query(
            Transaction.category,
            func.sum(Transaction.txn_amount_INR),
            func.count(Transaction.id),
        )
        .filter(
            Transaction.card_id == card_id,
            Transaction.txn_date >= from_date,
            Transaction.txn_date <= to_date,
            Transaction.txn_amount_INR.isnot(None),
        )
        .group_by(Transaction.category)
        .all()
    )

    return [
        CategorySpend(category=category, total_amount=total or Decimal("0"), transaction_count=count)
        for category, total, count in rows
    ]


@dataclass
class SpendSummary:
    total_amount: Decimal
    total_forex_markup_amount_INR: Decimal
    transaction_count: int


def get_card_spend_summary(
    session: Session,
    card_id: int,
    from_date: date,
    to_date: date,
    transaction_type: str,
    category: str | None = None,
) -> SpendSummary:
    """Aggregate a card's spend and forex markup over a date range, filtered
    by mandatory transaction_type ("domestic"/"international",
    case-insensitive) and optional category.

    Unlike get_card_category_breakdown, this never groups by category -- it
    always returns one aggregate row for whatever filters were given.

    Raises:
        CardNotFoundError: if card_id doesn't exist.
        InvalidDateRangeError: if from_date is after to_date.
        InvalidTransactionTypeError: if transaction_type isn't "domestic" or
            "international" (case-insensitive).
    """
    get_card(session, card_id)
    if from_date > to_date:
        raise InvalidDateRangeError(from_date, to_date)

    normalized_type = transaction_type.strip().lower()
    if normalized_type not in ("domestic", "international"):
        raise InvalidTransactionTypeError(transaction_type)

    query = session.query(
        func.sum(Transaction.txn_amount_INR),
        func.sum(Transaction.forex_markup_amount_INR),
        func.count(Transaction.id),
    ).filter(
        Transaction.card_id == card_id,
        Transaction.txn_date >= from_date,
        Transaction.txn_date <= to_date,
        Transaction.transaction_type == normalized_type,
    )
    if category is not None:
        query = query.filter(Transaction.category == category)

    total_amount, total_forex_markup, count = query.one()
    return SpendSummary(
        total_amount=total_amount or Decimal("0"),
        total_forex_markup_amount_INR=total_forex_markup or Decimal("0"),
        transaction_count=count or 0,
    )


class NoEligibleCardProductError(Exception):
    def __init__(self, card_id: int) -> None:
        self.card_id = card_id
        super().__init__(f"No eligible upgrade card product found for card: {card_id}")


# Trailing-12mo forex spend above which the recommendation's reason cites
# "high forex spending" rather than a generic lower-markup pitch -- an
# arbitrary but documented threshold, not derived from any external source.
HIGH_FOREX_SPEND_THRESHOLD_INR = Decimal("100000")


@dataclass
class CardRecommendation:
    current_card_id: int
    recommended_product: CardProduct
    trailing_12mo_forex_spend_inr: Decimal
    current_annual_markup: Decimal
    projected_annual_markup: Decimal
    projected_annual_savings: Decimal
    joining_fee: Decimal
    annual_fee: Decimal
    net_joining_fee: Decimal
    action_type: str
    reason: str


def recommend_card_upgrade(session: Session, card_id: int) -> CardRecommendation:
    """Recommend a lower-forex-markup active credit product and project the
    annual savings, using the card's own trailing-12-month forex history.

    The candidate is the active credit product with the lowest forex markup
    below the current card's (tie-break: lowest annual fee). Savings are
    markup-only (transactions no longer carry a stored GST figure).
    net_joining_fee is the candidate's own joining_fee with GST
    (DEFAULT_GST_RATE) added on top -- there is no relationship-tier
    discount anymore.

    Raises:
        CardNotFoundError: if card_id doesn't exist.
        NoEligibleCardProductError: if no active credit product beats the
            current card's forex markup.
    """
    card = get_card(session, card_id)
    current_product = get_card_product(session, card.card_product_id)

    candidate = (
        session.query(CardProduct)
        .filter(
            CardProduct.card_type == "credit",
            CardProduct.is_active.is_(True),
            CardProduct.forex_markup_pct < current_product.forex_markup_pct,
        )
        .order_by(CardProduct.forex_markup_pct.asc(), CardProduct.annual_fee.asc())
        .first()
    )
    if candidate is None:
        raise NoEligibleCardProductError(card_id)

    forex_summary = get_card_forex_summary(session, card_id)
    trailing_spend = forex_summary.total_forex_spend_inr
    current_total = forex_summary.total_markup_amount

    projected_markup = (trailing_spend * candidate.forex_markup_pct / Decimal("100")).quantize(Decimal("0.01"))
    projected_savings = current_total - projected_markup

    net_joining_fee = (candidate.joining_fee * (Decimal("1") + DEFAULT_GST_RATE)).quantize(Decimal("0.01"))

    action_type = "cross_sell" if candidate.card_type != current_product.card_type else "upgrade"
    if trailing_spend >= HIGH_FOREX_SPEND_THRESHOLD_INR:
        reason = (
            f"HIGH FOREX Spending: INR {trailing_spend:,.2f} spent abroad in the trailing 12 months, "
            f"costing INR {current_total:,.2f} in forex markup"
        )
    else:
        reason = (
            f"Lower forex markup available: {candidate.forex_markup_pct.normalize()}% vs. "
            f"{current_product.forex_markup_pct.normalize()}% on the current card"
        )

    logger.info(
        "recommend_card_upgrade: card=%s -> product=%s projected_savings=%s action_type=%s",
        card_id,
        candidate.id,
        projected_savings,
        action_type,
    )
    return CardRecommendation(
        current_card_id=card_id,
        recommended_product=candidate,
        trailing_12mo_forex_spend_inr=trailing_spend,
        current_annual_markup=current_total,
        projected_annual_markup=projected_markup,
        projected_annual_savings=projected_savings,
        joining_fee=candidate.joining_fee,
        annual_fee=candidate.annual_fee,
        net_joining_fee=net_joining_fee,
        action_type=action_type,
        reason=reason,
    )
