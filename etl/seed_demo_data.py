"""Seeds the fictional "Digital RM Twin" demo dataset.

This is entirely synthetic demo content built from the RM Twin demo script
(Mr. Mehta, the Wisdom Property / DoubleTree Hilton dispute, the Global
Elite Zero Forex Card pitch) -- it is NOT the real personal HDFC statement
data imported by etl/excel_importer.py. It is seeded under its own
account number (DEFAULT_DEMO_ACCOUNT_NUMBER) specifically so it never mixes
with that real account's rows.

Usage (from the project root, C:\\RMA):
    python -m etl.seed_demo_data
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session, sessionmaker

from api.repository.cards import encode_reward_transfer_partners
from config.logging_config import configure_logging
from db.models import Account, Card, CardProduct, Customer, Merchant, MerchantAlias, Transaction
from db.session import SessionLocal, init_db

logger = logging.getLogger(__name__)

DEFAULT_DEMO_ACCOUNT_NUMBER = "DEMO-MEHTA-01"

WISDOM_PROPERTY_DESCRIPTOR = "WISDOM PROPERTY NL II"

# 15 debit-card forex transactions over the trailing 12 months, matching the
# demo script's own figures: they sum to INR 380,000 (~Rs.3.8L, the script's
# "you've spent about Rs.3.8 lakh in foreign-currency") at a 3.5% markup +
# 18% GST, which nets to Rs.15,694 (~Rs.15.7K, the script's "roughly Rs.15,700
# extra"). Each (days_ago, currency, fx_amount, inr_amount, category,
# narration) row is a days-before-"today" offset so the trailing-365-day
# forex summary picks all of them up regardless of which day this is seeded.
# The Wisdom Property row is pinned to exactly the script's "12 Aug" /
# "353 euros" / "32,000 INR" figures.
_FOREX_TRANSACTIONS = [
    (351, "USD", Decimal("300.00"), Decimal("25000.00"), "Shopping", "AMAZON.COM AMZN.COM/BILL WA"),
    (330, "GBP", Decimal("150.00"), Decimal("18000.00"), "Dining", "THE IVY LONDON"),
    (307, "EUR", Decimal("270.00"), Decimal("30000.00"), "Travel", "ACCOR HOTELS PARIS"),
    (285, "USD", Decimal("250.00"), Decimal("22000.00"), "Travel", "MARRIOTT DUBAI INTL"),
    (264, "EUR", Decimal("150.00"), Decimal("15000.00"), "Dining", "CAFE DE PARIS SARL"),
    (229, "GBP", Decimal("250.00"), Decimal("28000.00"), "Travel", "BRITISH AIRWAYS PLC"),
    (193, "USD", Decimal("220.00"), Decimal("20000.00"), "Shopping", "APPLE STORE R512 NYC"),
    (169, "SGD", Decimal("400.00"), Decimal("32000.00"), "Dining", "MARINA BAY SANDS SG"),
    (153, "USD", Decimal("280.00"), Decimal("24000.00"), "Transport", "UBER TRIP US SF"),
    (129, "EUR", Decimal("190.00"), Decimal("19000.00"), "Shopping", "GALERIES LAFAYETTE PARIS"),
    (111, "GBP", Decimal("210.00"), Decimal("26000.00"), "Travel", "HILTON LONDON METROPOLE"),
    (85, "USD", Decimal("240.00"), Decimal("21000.00"), "Dining", "NOBU RESTAURANT NYC"),
    (63, "EUR", Decimal("160.00"), Decimal("17000.00"), "Shopping", "ZARA ESPANA SA"),
    (50, "EUR", Decimal("353.00"), Decimal("32000.00"), "Travel", WISDOM_PROPERTY_DESCRIPTOR),
    (26, "USD", Decimal("580.00"), Decimal("51000.00"), "Travel", "EMIRATES AIRLINE NYC"),
]

FOREX_MARKUP_PCT = Decimal("3.5")
GST_RATE = Decimal("0.18")


@dataclass
class SeedResult:
    merchants_inserted: int = 0
    aliases_inserted: int = 0
    card_products_inserted: int = 0
    cards_inserted: int = 0
    customers_inserted: int = 0
    transactions_inserted: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


def _get_or_create_account(session: Session, account_number: str) -> Account:
    account = session.get(Account, account_number)
    if account is None:
        account = Account(account_number=account_number, display_name="Demo - Mr. Mehta (RM Twin script)")
        session.add(account)
        session.flush()
    return account


def _seed_merchant(session: Session, result: SeedResult) -> Merchant:
    merchant = (
        session.query(Merchant)
        .filter_by(brand_name="DoubleTree by Hilton Amsterdam Centraal Station", city="Amsterdam")
        .first()
    )
    if merchant is not None:
        result.skipped += 1
        return merchant

    merchant = Merchant(
        brand_name="DoubleTree by Hilton Amsterdam Centraal Station",
        associated_property="DoubleTree by Hilton Amsterdam Centraal Station",
        mcc="7011",
        category="Travel",
        sub_category="Hotel",
        city="Amsterdam",
        country="NL",
    )
    session.add(merchant)
    session.flush()
    result.merchants_inserted += 1
    return merchant


def _seed_merchant_alias(session: Session, merchant_id: int, raw_pattern: str, result: SeedResult) -> None:
    existing = session.query(MerchantAlias).filter_by(raw_pattern=raw_pattern).first()
    if existing is not None:
        result.skipped += 1
        return

    session.add(MerchantAlias(merchant_id=merchant_id, raw_pattern=raw_pattern))
    result.aliases_inserted += 1


def _seed_card_products(session: Session, result: SeedResult) -> tuple[CardProduct, CardProduct]:
    debit_product = session.query(CardProduct).filter_by(name="HDFC Debit Card").first()
    if debit_product is None:
        debit_product = CardProduct(
            name="HDFC Debit Card",
            network="Visa",
            card_type="debit",
            forex_markup_pct=FOREX_MARKUP_PCT,
        )
        session.add(debit_product)
        session.flush()
        result.card_products_inserted += 1
    else:
        result.skipped += 1

    credit_product = session.query(CardProduct).filter_by(name="Global Elite Zero Forex Card").first()
    if credit_product is None:
        credit_product = CardProduct(
            name="Global Elite Zero Forex Card",
            network="Visa",
            card_type="credit",
            forex_markup_pct=Decimal("0"),
            joining_fee=Decimal("15000.00"),
            annual_fee=Decimal("15000.00"),
            # None here specifically means "unlimited" for this product
            # (matches the script's "unlimited complimentary lounge access"),
            # not "not applicable" -- this product is known to have lounge
            # access at all, unlike e.g. a plain debit product.
            lounge_visits_domestic_per_year=None,
            lounge_visits_international_per_year=None,
            guest_visits_per_year=12,
            reward_transfer_partners=encode_reward_transfer_partners(
                ["Air India Maharaja Club", "Flying Blue", "Accor ALL"]
            ),
            min_relationship_tier_for_discount="PRIORITY",
            relationship_discount_pct=Decimal("25.00"),
        )
        session.add(credit_product)
        session.flush()
        result.card_products_inserted += 1
    else:
        result.skipped += 1

    return debit_product, credit_product


def _seed_card(session: Session, account_number: str, card_product_id: int, result: SeedResult) -> Card:
    card = session.query(Card).filter_by(account_number=account_number, last4="4821", network="Visa").first()
    if card is not None:
        result.skipped += 1
        return card

    card = Card(
        account_number=account_number,
        card_product_id=card_product_id,
        last4="4821",
        network="Visa",
        card_type="debit",
    )
    session.add(card)
    session.flush()
    result.cards_inserted += 1
    return card


def _seed_customer(session: Session, account_number: str, result: SeedResult) -> Customer:
    customer = session.query(Customer).filter_by(account_number=account_number).first()
    if customer is not None:
        result.skipped += 1
        return customer

    customer = Customer(
        account_number=account_number,
        full_name="Mr. Mehta",
        registered_email="mehta@gmail.com",
        alt_email="rammehta@outlook.com",
        relationship_tier="PRIORITY",
        delivery_address_office="BCG Office, Level 12, Nariman Point, Mumbai",
        delivery_address_home="Residence on file",
        preferred_delivery_address_type="OFFICE",
    )
    session.add(customer)
    session.flush()
    result.customers_inserted += 1
    return customer


def _seed_transactions(
    session: Session,
    account_number: str,
    card_id: int,
    merchant_id: int,
    result: SeedResult,
    as_of: date,
) -> None:
    running_balance = Decimal("500000.00")

    # Oldest first, so the running closing_balance decreases in a sensible order.
    for i, (days_ago, currency, fx_amount, inr_amount, category, narration) in enumerate(
        sorted(_FOREX_TRANSACTIONS, key=lambda row: row[0], reverse=True)
    ):
        reference_no = f"SEED-FX-{i:03d}"
        txn_date = as_of - timedelta(days=days_ago)

        existing = (
            session.query(Transaction)
            .filter_by(account_number=account_number, reference_no=reference_no, txn_date=txn_date)
            .first()
        )
        if existing is not None:
            result.skipped += 1
            continue

        markup = (inr_amount * FOREX_MARKUP_PCT / Decimal("100")).quantize(Decimal("0.01"))
        gst = (markup * GST_RATE).quantize(Decimal("0.01"))
        running_balance -= inr_amount

        session.add(
            Transaction(
                account_number=account_number,
                card_id=card_id,
                merchant_id=merchant_id if narration == WISDOM_PROPERTY_DESCRIPTOR else None,
                txn_date=txn_date,
                value_date=txn_date,
                narration=narration,
                reference_no=reference_no,
                withdrawal_amount=inr_amount,
                closing_balance=running_balance,
                txn_currency=currency,
                txn_amount=fx_amount,
                exchange_rate=(inr_amount / fx_amount).quantize(Decimal("0.0001")),
                forex_markup_pct=FOREX_MARKUP_PCT,
                forex_markup_amount=markup,
                gst_on_markup=gst,
                category=category,
            )
        )
        result.transactions_inserted += 1


def seed_demo_data(
    account_number: str = DEFAULT_DEMO_ACCOUNT_NUMBER,
    session_factory: sessionmaker | None = None,
    as_of: date | None = None,
) -> SeedResult:
    """Seed the fictional RM Twin demo dataset under account_number.

    Idempotent: re-running skips anything that already exists (matched by
    the same natural keys used elsewhere in this codebase -- card product
    name, card identity, merchant identity/alias, one customer per account,
    and the transaction dedup key already enforced by the schema).
    """
    factory = session_factory or SessionLocal
    result = SeedResult()
    resolved_as_of = as_of or date.today()

    with factory() as session:
        _get_or_create_account(session, account_number)
        merchant = _seed_merchant(session, result)
        _seed_merchant_alias(session, merchant.id, WISDOM_PROPERTY_DESCRIPTOR, result)
        debit_product, _credit_product = _seed_card_products(session, result)
        card = _seed_card(session, account_number, debit_product.id, result)
        _seed_customer(session, account_number, result)
        _seed_transactions(session, account_number, card.id, merchant.id, result, resolved_as_of)

        session.commit()

    logger.info(
        "seed_demo_data complete: merchants=%s aliases=%s card_products=%s cards=%s "
        "customers=%s transactions=%s skipped=%s errors=%s",
        result.merchants_inserted,
        result.aliases_inserted,
        result.card_products_inserted,
        result.cards_inserted,
        result.customers_inserted,
        result.transactions_inserted,
        result.skipped,
        len(result.errors),
    )
    return result


def main() -> None:
    configure_logging()
    init_db()
    result = seed_demo_data()

    print(
        f"Merchants: {result.merchants_inserted}, Aliases: {result.aliases_inserted}, "
        f"Card products: {result.card_products_inserted}, Cards: {result.cards_inserted}, "
        f"Customers: {result.customers_inserted}, Transactions: {result.transactions_inserted}, "
        f"Skipped: {result.skipped}, Errors: {len(result.errors)}"
    )
    for err in result.errors:
        print(" -", err)


if __name__ == "__main__":
    main()
