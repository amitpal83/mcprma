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

from api.repository.customers import CustomerNotFoundError, get_customer_by_account
from api.repository.merchants import MerchantNotResolvedError, resolve_merchant
from api.repository.transactions import AccountNotFoundError, InvalidDateRangeError
from db.models import Account, Card, CardProduct, Transaction

logger = logging.getLogger(__name__)

# Fallback GST-on-markup rate, used only when a card has no forex history to
# derive an effective rate from (can't divide by a zero markup total).
DEFAULT_GST_RATE = Decimal("0.18")

# Ordinal ranking for "does this customer's tier meet the minimum required
# for a discount" -- a simple, demo-scoped hierarchy, not a general-purpose
# tier registry.
_RELATIONSHIP_TIER_RANK = {"STANDARD": 0, "PRIORITY": 1, "PREMIUM": 2, "PRIVATE": 3}


def _tier_meets_minimum(customer_tier: str | None, min_tier: str | None) -> bool:
    if min_tier is None:
        return True
    if customer_tier is None:
        return False
    return _RELATIONSHIP_TIER_RANK.get(customer_tier, -1) >= _RELATIONSHIP_TIER_RANK.get(min_tier, 0)


class CardNotFoundError(Exception):
    def __init__(self, card_id: int) -> None:
        self.card_id = card_id
        super().__init__(f"Card not found: {card_id}")


class CardProductNotFoundError(Exception):
    def __init__(self, card_product_id: int) -> None:
        self.card_product_id = card_product_id
        super().__init__(f"Card product not found: {card_product_id}")


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
    amount (txn_amount) OR the INR-settled amount (withdrawal_amount), since
    a caller searching "~350" doesn't know in advance which currency the
    transaction they're thinking of was in.

    merchant_text first tries to resolve to a canonical merchant (so
    "Wisdom Property" finds a transaction whose raw narration is "WISDOM
    PROPERTY NL II"); if it can't be resolved, falls back to a
    case-insensitive substring match against the raw narration.

    Raises:
        CardNotFoundError: if card_id doesn't exist.
        InvalidDateRangeError: if from_date is after to_date.
    """
    get_card(session, card_id)
    if from_date > to_date:
        raise InvalidDateRangeError(from_date, to_date)

    query = session.query(Transaction).filter(
        Transaction.card_id == card_id,
        Transaction.txn_date >= from_date,
        Transaction.txn_date <= to_date,
    )

    if amount_min is not None or amount_max is not None:
        conditions = []
        for column in (Transaction.txn_amount, Transaction.withdrawal_amount):
            condition = column.isnot(None)
            if amount_min is not None:
                condition = condition & (column >= amount_min)
            if amount_max is not None:
                condition = condition & (column <= amount_max)
            conditions.append(condition)
        query = query.filter(or_(*conditions))

    if merchant_text:
        try:
            merchant = resolve_merchant(session, merchant_text)
        except MerchantNotResolvedError:
            query = query.filter(Transaction.narration.ilike(f"%{merchant_text}%"))
        else:
            query = query.filter(Transaction.merchant_id == merchant.id)

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
    total_gst_amount: Decimal
    total_markup_and_gst: Decimal
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
            Transaction.txn_currency.isnot(None),
            Transaction.txn_date >= window_start,
            Transaction.txn_date <= as_of,
        )
        .all()
    )

    total_spend = sum((row.withdrawal_amount or Decimal("0") for row in rows), Decimal("0"))
    total_markup = sum((row.forex_markup_amount or Decimal("0") for row in rows), Decimal("0"))
    total_gst = sum((row.gst_on_markup or Decimal("0") for row in rows), Decimal("0"))

    logger.info(
        "get_card_forex_summary: card=%s window=[%s,%s] -> spend=%s markup=%s gst=%s (%s txns)",
        card_id,
        window_start,
        as_of,
        total_spend,
        total_markup,
        total_gst,
        len(rows),
    )
    return ForexSummary(
        card_id=card_id,
        from_date=window_start,
        to_date=as_of,
        total_forex_spend_inr=total_spend,
        total_markup_amount=total_markup,
        total_gst_amount=total_gst,
        total_markup_and_gst=total_markup + total_gst,
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
            func.sum(Transaction.withdrawal_amount),
            func.count(Transaction.id),
        )
        .filter(
            Transaction.card_id == card_id,
            Transaction.txn_date >= from_date,
            Transaction.txn_date <= to_date,
            Transaction.withdrawal_amount.isnot(None),
        )
        .group_by(Transaction.category)
        .all()
    )

    return [
        CategorySpend(category=category, total_amount=total or Decimal("0"), transaction_count=count)
        for category, total, count in rows
    ]


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
    current_annual_markup_and_gst: Decimal
    projected_annual_markup_and_gst: Decimal
    projected_annual_savings: Decimal
    joining_fee: Decimal
    annual_fee: Decimal
    discount_pct_applied: Decimal | None
    net_joining_fee_after_discount: Decimal
    action_type: str
    reason: str
    applicable_discounts: str | None


def recommend_card_upgrade(session: Session, card_id: int) -> CardRecommendation:
    """Recommend a lower-forex-markup active credit product and project the
    annual savings, using the card's own trailing-12-month forex history.

    The candidate is the active credit product with the lowest forex markup
    below the current card's (tie-break: lowest annual fee). The projection
    reuses the *current* card's own derived GST-on-markup rate (gst/markup
    from its trailing 12 months) rather than a hardcoded rate, so it stays
    consistent with whatever real data backs it; a fallback constant
    (DEFAULT_GST_RATE) only applies if the card has no forex history yet.
    A relationship-tier discount on the joining fee is applied if the
    customer's profile tier meets the candidate's minimum, then GST is
    added back on the net fee.

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
    current_total = forex_summary.total_markup_amount + forex_summary.total_gst_amount

    if forex_summary.total_markup_amount > 0:
        effective_gst_rate = forex_summary.total_gst_amount / forex_summary.total_markup_amount
    else:
        effective_gst_rate = DEFAULT_GST_RATE

    projected_markup = (trailing_spend * candidate.forex_markup_pct / Decimal("100")).quantize(Decimal("0.01"))
    projected_gst = (projected_markup * effective_gst_rate).quantize(Decimal("0.01"))
    projected_total = projected_markup + projected_gst
    projected_savings = current_total - projected_total

    try:
        customer_tier = get_customer_by_account(session, card.account_number).relationship_tier
    except CustomerNotFoundError:
        customer_tier = None

    discount_pct_applied = None
    discounted_joining_fee = candidate.joining_fee
    if candidate.relationship_discount_pct is not None and _tier_meets_minimum(
        customer_tier, candidate.min_relationship_tier_for_discount
    ):
        discount_pct_applied = candidate.relationship_discount_pct
        discounted_joining_fee = candidate.joining_fee * (Decimal("1") - discount_pct_applied / Decimal("100"))

    net_joining_fee_after_discount = (discounted_joining_fee * (Decimal("1") + effective_gst_rate)).quantize(
        Decimal("0.01")
    )

    action_type = "cross_sell" if candidate.card_type != current_product.card_type else "upgrade"
    applicable_discounts = (
        f"{discount_pct_applied.normalize()}% on joining fee" if discount_pct_applied is not None else None
    )
    if trailing_spend >= HIGH_FOREX_SPEND_THRESHOLD_INR:
        reason = (
            f"HIGH FOREX Spending: INR {trailing_spend:,.2f} spent abroad in the trailing 12 months, "
            f"costing INR {current_total:,.2f} in forex markup + GST"
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
        current_annual_markup_and_gst=current_total,
        projected_annual_markup_and_gst=projected_total,
        projected_annual_savings=projected_savings,
        joining_fee=candidate.joining_fee,
        annual_fee=candidate.annual_fee,
        discount_pct_applied=discount_pct_applied,
        net_joining_fee_after_discount=net_joining_fee_after_discount,
        action_type=action_type,
        reason=reason,
        applicable_discounts=applicable_discounts,
    )
