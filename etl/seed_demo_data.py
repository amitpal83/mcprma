"""Seeds the "Digital RM Twin" demo dataset.

This is synthetic demo content built from the RM Twin demo script and the
Customer360/Product_Catalogue source documents (Vipul Singh, the Wisdom
Property / DoubleTree Hilton dispute, the Global Elite zero forex markup
credit card pitch) -- it is NOT the real personal HDFC statement data
imported by etl/excel_importer.py. It is seeded under its own account number
(DEFAULT_DEMO_ACCOUNT_NUMBER) specifically so it never mixes with that real
account's rows.

Usage (from the project root, C:\\RMA):
    python -m etl.seed_demo_data
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session, sessionmaker

from api.repository.cards import (
    encode_eligibility_criteria,
    encode_key_features,
    encode_reward_transfer_partners,
)
from config.logging_config import configure_logging
from data.transactionlist import TRANSACTIONS
from db.models import (
    Account,
    Card,
    CardProduct,
    Customer,
    Customer360,
    Dispute,
    Merchant,
    MerchantAlias,
    ServiceRequest,
    Transaction,
)
from db.session import SessionLocal, init_db

logger = logging.getLogger(__name__)

DEFAULT_DEMO_ACCOUNT_NUMBER = "ACC101"

WISDOM_PROPERTY_DESCRIPTOR = "WISDOM PROPERTY NL II"

# Deliberately empty: this used to hold 15 scripted demo forex transactions
# (days_ago, currency, fx_amount, inr_amount, category, narration,
# merchant_location, kiosk) summing to INR 380,000, dropped from the
# canonical demo dataset. seed_demo_data() prunes any such SEED-FX-* rows
# left over from when this list was populated, on every run.
_FOREX_TRANSACTIONS: list[tuple] = []

FOREX_MARKUP_PCT = Decimal("3.5")
GST_RATE = Decimal("0.18")

# Currency per foreign merchant_location found in data/transactionlist.py,
# used only to back-derive a realistic txn_amount/exchange_rate for that
# imported history -- the INR ledger amounts (withdrawal_amount,
# forex_markup_amount) come straight from that file's own amount/
# forex_charges figures regardless of this mapping.
_LOCATION_CURRENCY = {
    "New York": "USD",
    "New Jersey": "USD",
    "JFK Airport": "USD",
    "London": "GBP",
    "London Heathrow Airport": "GBP",
    "Singapore": "SGD",
    "Singapore Changi Airport": "SGD",
    "Dubai": "AED",
    "Dubai International Airport": "AED",
    "Bangkok": "THB",
    "Bangkok Suvarnabhumi Airport": "THB",
    "Paris": "EUR",
}

# Approximate illustrative market rates (INR per unit foreign currency).
_FX_RATES_INR = {
    "USD": Decimal("83.00"),
    "GBP": Decimal("105.00"),
    "SGD": Decimal("62.00"),
    "AED": Decimal("22.60"),
    "THB": Decimal("2.35"),
    "EUR": Decimal("90.00"),
}

# The complete Customer360.json source payload, kept verbatim so it can be
# stored as-is in Customer360.raw_json as well as driving every other seeded
# field derived from it.
_CUSTOMER_360_SOURCE_JSON: dict = {
    "customer_name": "VIPUL SINGH",
    "onboarding_date": "2023-01-15",
    "email_work": "work@email.com",
    "email_personal": "personal@email.com",
    "address": [
        {
            "address_type": "Bank Branch",
            "address": "HDFC bank, VIPUL SINGH, 123 Main Street, New Delhi, India",
            "preferred_flag": True,
        },
        {
            "address_type": "home",
            "address": "Sector 12, Noida, Uttar Pradesh, India",
            "preferred_flag": False,
        },
    ],
    "latest_service_requests": {
        "service_request_id": "SR16788",
        "service_request_type": "account_statement",
        "service_request_date": "2026-10-01",
        "service_request_status": "under progress",
        "service_request_details": "Account Statement has been Dispatched via courier, expected delivery by 2026-10-07",
        "service_request_delivery_address_type": "Bank Branch",
    },
    "current_instruments": [
        {
            "instrument_type": "debit_card",
            "instrument_name": "HDFC Bank Debit Card",
            "instrument_identifier": "4881",
            "instrument_expiry": "2031-08-31",
            "instrument_last_kyc": "2025-12-31",
        },
        {
            "instrument_type": "salary savings account",
            "instrument_name": "HDFC Bank Salary Savings Account",
            "instrument_identifier": "1156788",
            "instrument_last_kyc": "2025-12-31",
            "instrument_expiry": None,
        },
    ],
    "next_best_offer": {
        "recommended_product_id": "prod-2",
        "action_type": "cross_sell",
        "applicable_discounts": "25% on joining fee",
        "reason": "HIGH FOREX Spending",
    },
}


@dataclass
class SeedResult:
    merchants_inserted: int = 0
    aliases_inserted: int = 0
    card_products_inserted: int = 0
    cards_inserted: int = 0
    customers_inserted: int = 0
    transactions_inserted: int = 0
    transactions_pruned: int = 0
    service_requests_inserted: int = 0
    service_requests_pruned: int = 0
    customer_360_inserted: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


def _get_or_create_account(session: Session, account_number: str) -> Account:
    account = session.get(Account, account_number)
    if account is None:
        account = Account(account_number=account_number, display_name="Demo - Vipul Singh (RM Twin script)")
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
    """Seed/update the two catalogue products from Product_Catalogue.json.

    Looked up by new name, then external_product_id, then the legacy name
    this demo used before the catalogue json existed -- so re-running this
    against an older-seeded database updates that same row in place instead
    of creating a duplicate.
    """
    debit_product = (
        session.query(CardProduct).filter_by(name="HDFC Bank Debit Card").first()
        or session.query(CardProduct).filter_by(external_product_id="prod-1").first()
        or session.query(CardProduct).filter_by(name="HDFC Debit Card").first()
    )
    debit_key_features = encode_key_features(
        [
            "Contactless payments",
            "EMV chip technology",
            "Online banking access",
            "24/7 customer support",
        ]
    )
    debit_eligibility = encode_eligibility_criteria(
        [
            "Must be an HDFC Bank account holder",
            "Minimum age of 18 years",
            "Valid government-issued ID",
        ]
    )
    if debit_product is None:
        debit_product = CardProduct(
            name="HDFC Bank Debit Card",
            network="Visa",
            card_type="debit",
            forex_markup_pct=FOREX_MARKUP_PCT,
            external_product_id="prod-1",
            forex_enabled=True,
            product_rewards_enabled=True,
            key_features=debit_key_features,
            eligibility_criteria=debit_eligibility,
        )
        session.add(debit_product)
        session.flush()
        result.card_products_inserted += 1
    else:
        debit_product.name = "HDFC Bank Debit Card"
        debit_product.external_product_id = "prod-1"
        debit_product.forex_enabled = True
        debit_product.product_rewards_enabled = True
        debit_product.key_features = debit_key_features
        debit_product.eligibility_criteria = debit_eligibility
        result.skipped += 1

    credit_product = (
        session.query(CardProduct).filter_by(name="Global Elite zero forex markup credit card").first()
        or session.query(CardProduct).filter_by(external_product_id="prod-2").first()
        or session.query(CardProduct).filter_by(name="Global Elite Zero Forex Card").first()
    )
    credit_key_features = encode_key_features(
        [
            "Zero forex markup on international transactions",
            "Premium concierge services",
            "Access to exclusive airport lounges",
            "Comprehensive travel insurance coverage",
        ]
    )
    credit_eligibility = encode_eligibility_criteria(["Minimum annual income of INR 25 lakhs"])
    if credit_product is None:
        credit_product = CardProduct(
            name="Global Elite zero forex markup credit card",
            network="Visa",
            card_type="credit",
            forex_markup_pct=Decimal("0"),
            joining_fee=Decimal("15000.00"),
            annual_fee=Decimal("0.00"),
            # None here specifically means "unlimited" for this product
            # (matches the demo script's "unlimited complimentary lounge
            # access"), not "not applicable".
            lounge_visits_domestic_per_year=None,
            lounge_visits_international_per_year=None,
            guest_visits_per_year=12,
            reward_transfer_partners=encode_reward_transfer_partners(
                ["Air India Maharaja Club", "Flying Blue", "Accor ALL"]
            ),
            min_relationship_tier_for_discount="PRIORITY",
            relationship_discount_pct=Decimal("25.00"),
            external_product_id="prod-2",
            forex_enabled=False,
            product_rewards_enabled=False,
            key_features=credit_key_features,
            eligibility_criteria=credit_eligibility,
        )
        session.add(credit_product)
        session.flush()
        result.card_products_inserted += 1
    else:
        credit_product.name = "Global Elite zero forex markup credit card"
        credit_product.annual_fee = Decimal("0.00")
        credit_product.external_product_id = "prod-2"
        credit_product.forex_enabled = False
        credit_product.product_rewards_enabled = False
        credit_product.key_features = credit_key_features
        credit_product.eligibility_criteria = credit_eligibility
        result.skipped += 1

    return debit_product, credit_product


def _seed_card(session: Session, account_number: str, card_product_id: int, result: SeedResult) -> Card:
    card = (
        session.query(Card).filter_by(account_number=account_number, last4="4881", network="Visa").first()
        or session.query(Card).filter_by(account_number=account_number, last4="4821", network="Visa").first()
    )
    if card is not None:
        card.last4 = "4881"
        card.card_product_id = card_product_id
        card.expiry_date = date(2031, 8, 31)
        card.last_kyc_date = date(2025, 12, 31)
        result.skipped += 1
        return card

    card = Card(
        account_number=account_number,
        card_product_id=card_product_id,
        last4="4881",
        network="Visa",
        card_type="debit",
        expiry_date=date(2031, 8, 31),
        last_kyc_date=date(2025, 12, 31),
    )
    session.add(card)
    session.flush()
    result.cards_inserted += 1
    return card


def _seed_customer(session: Session, account_number: str, result: SeedResult) -> Customer:
    customer = session.query(Customer).filter_by(account_number=account_number).first()
    if customer is not None:
        customer.full_name = "Vipul Singh"
        customer.registered_email = "work@email.com"
        customer.alt_email = "personal@email.com"
        customer.email_work = "work@email.com"
        customer.email_personal = "personal@email.com"
        customer.onboarding_date = date(2023, 1, 15)
        customer.relationship_tier = "PRIORITY"
        customer.delivery_address_office = "HDFC bank, VIPUL SINGH, 123 Main Street, New Delhi, India"
        customer.delivery_address_home = "Sector 12, Noida, Uttar Pradesh, India"
        customer.preferred_delivery_address_type = "OFFICE"
        result.skipped += 1
        return customer

    customer = Customer(
        account_number=account_number,
        full_name="Vipul Singh",
        registered_email="work@email.com",
        alt_email="personal@email.com",
        email_work="work@email.com",
        email_personal="personal@email.com",
        onboarding_date=date(2023, 1, 15),
        relationship_tier="PRIORITY",
        delivery_address_office="HDFC bank, VIPUL SINGH, 123 Main Street, New Delhi, India",
        delivery_address_home="Sector 12, Noida, Uttar Pradesh, India",
        preferred_delivery_address_type="OFFICE",
    )
    session.add(customer)
    session.flush()
    result.customers_inserted += 1
    return customer


def _build_forex_rows(as_of: date) -> list[dict]:
    """The 15 scripted forex transactions, as merge-sortable row dicts."""
    rows = []
    for i, (days_ago, currency, fx_amount, inr_amount, category, narration, location, kiosk) in enumerate(
        _FOREX_TRANSACTIONS
    ):
        txn_date = as_of - timedelta(days=days_ago)
        markup = (inr_amount * FOREX_MARKUP_PCT / Decimal("100")).quantize(Decimal("0.01"))
        gst = (markup * GST_RATE).quantize(Decimal("0.01"))
        rows.append(
            {
                "reference_no": f"SEED-FX-{i:03d}",
                "txn_date": txn_date,
                "narration": narration,
                "withdrawal_amount": inr_amount,
                "txn_currency": currency,
                "txn_amount": fx_amount,
                "exchange_rate": (inr_amount / fx_amount).quantize(Decimal("0.0001")),
                "forex_markup_pct": FOREX_MARKUP_PCT,
                "forex_markup_amount": markup,
                "gst_on_markup": gst,
                "category": category,
                "description": f"Overseas {category.lower()} spend",
                "kiosk": kiosk,
                "merchant_location": location,
                "instrument_mode": "card-last4digits-4881",
                "transaction_type": "international",
                "is_wisdom_property": narration == WISDOM_PROPERTY_DESCRIPTOR,
            }
        )
    return rows


def _build_imported_rows() -> list[dict]:
    """The 365-day history from data/transactionlist.py, as merge-sortable row dicts.

    amount/forex_charges are taken as already INR-settled (forex_charges is
    always exactly 3.5% of amount for international rows in that file); a
    realistic market currency + FX rate per merchant_location is used only to
    back-derive txn_currency/txn_amount/exchange_rate, not to rescale the INR
    ledger amounts themselves.
    """
    rows = []
    for row in TRANSACTIONS:
        txn_date = date.fromisoformat(row["date"])
        withdrawal_amount = Decimal(str(row["amount"])).quantize(Decimal("0.01"))
        transaction_type = row["transaction_type"]

        txn_currency = None
        txn_amount = None
        exchange_rate = None
        forex_markup_pct = None
        forex_markup_amount = None
        gst_on_markup = None

        if transaction_type == "international":
            currency = _LOCATION_CURRENCY.get(row["merchant_location"], "USD")
            rate = _FX_RATES_INR[currency]
            txn_currency = currency
            exchange_rate = rate
            txn_amount = (withdrawal_amount / rate).quantize(Decimal("0.01"))
            forex_markup_pct = FOREX_MARKUP_PCT
            forex_markup_amount = Decimal(str(row["forex_charges"])).quantize(Decimal("0.01"))
            gst_on_markup = (forex_markup_amount * GST_RATE).quantize(Decimal("0.01"))

        rows.append(
            {
                "reference_no": row["id"],
                "txn_date": txn_date,
                "narration": row["merchant"],
                "withdrawal_amount": withdrawal_amount,
                "txn_currency": txn_currency,
                "txn_amount": txn_amount,
                "exchange_rate": exchange_rate,
                "forex_markup_pct": forex_markup_pct,
                "forex_markup_amount": forex_markup_amount,
                "gst_on_markup": gst_on_markup,
                "category": row["category"],
                "description": row["description"],
                "kiosk": row["kiosk"],
                "merchant_location": row["merchant_location"],
                "instrument_mode": row["instrument_mode"],
                "transaction_type": transaction_type,
                "is_wisdom_property": False,
            }
        )
    return rows


def _seed_transactions(
    session: Session,
    account_number: str,
    card_id: int,
    merchant_id: int,
    result: SeedResult,
    as_of: date,
) -> None:
    """Seed the full transaction history: the 15 scripted forex rows plus the
    365-day history from data/transactionlist.py.

    Both sets are merge-sorted by txn_date into one sequence so closing_balance
    is a single consistent running total across the whole history, starting
    from a balance comfortably larger than total withdrawals (1.1x the sum)
    rather than a hardcoded guess.

    Also prunes any previously-seeded transaction under this account that no
    longer appears in the current source data (e.g. a row dropped from
    _FOREX_TRANSACTIONS or data/transactionlist.py), so a database that was
    seeded from an older version of this file converges to match the current
    code on every re-run -- this is what lets `server.py` call seed_demo_data()
    on every startup instead of needing a one-off manual fix per deployment.
    Scoped strictly to account_number (the demo account), never touching
    the real imported statement seeded under a different account.
    """
    all_rows = _build_forex_rows(as_of) + _build_imported_rows()
    all_rows.sort(key=lambda r: r["txn_date"])

    expected_reference_nos = {row["reference_no"] for row in all_rows}
    stale_transactions = (
        session.query(Transaction)
        .filter(
            Transaction.account_number == account_number,
            ~Transaction.reference_no.in_(expected_reference_nos),
        )
        .all()
    )
    for txn in stale_transactions:
        session.query(Dispute).filter_by(transaction_id=txn.id).delete(synchronize_session=False)
        session.delete(txn)
        result.transactions_pruned += 1

    running_balance = (sum((r["withdrawal_amount"] for r in all_rows), Decimal("0")) * Decimal("1.1")).quantize(
        Decimal("0.01")
    )

    for row in all_rows:
        running_balance -= row["withdrawal_amount"]

        existing = (
            session.query(Transaction)
            .filter_by(account_number=account_number, reference_no=row["reference_no"], txn_date=row["txn_date"])
            .first()
        )
        if existing is not None:
            result.skipped += 1
            continue

        session.add(
            Transaction(
                account_number=account_number,
                card_id=card_id,
                merchant_id=merchant_id if row["is_wisdom_property"] else None,
                txn_date=row["txn_date"],
                value_date=row["txn_date"],
                narration=row["narration"],
                reference_no=row["reference_no"],
                withdrawal_amount=row["withdrawal_amount"],
                closing_balance=running_balance,
                txn_currency=row["txn_currency"],
                txn_amount=row["txn_amount"],
                exchange_rate=row["exchange_rate"],
                forex_markup_pct=row["forex_markup_pct"],
                forex_markup_amount=row["forex_markup_amount"],
                gst_on_markup=row["gst_on_markup"],
                category=row["category"],
                description=row["description"],
                kiosk=row["kiosk"],
                merchant_location=row["merchant_location"],
                instrument_mode=row["instrument_mode"],
                transaction_type=row["transaction_type"],
            )
        )
        result.transactions_inserted += 1


def _seed_service_request(session: Session, customer_id: int, result: SeedResult) -> ServiceRequest:
    source = _CUSTOMER_360_SOURCE_JSON["latest_service_requests"]
    service_request = session.query(ServiceRequest).filter_by(service_request_id=source["service_request_id"]).first()
    if service_request is not None:
        result.skipped += 1
        return service_request

    service_request = ServiceRequest(
        customer_id=customer_id,
        service_request_id=source["service_request_id"],
        service_request_type=source["service_request_type"],
        service_request_date=date.fromisoformat(source["service_request_date"]),
        service_request_status=source["service_request_status"],
        service_request_details=source["service_request_details"],
        service_request_delivery_address_type=source["service_request_delivery_address_type"],
    )
    session.add(service_request)
    session.flush()
    result.service_requests_inserted += 1
    return service_request


def _prune_stale_service_requests(
    session: Session, customer_id: int, canonical_service_request_id: str, result: SeedResult
) -> None:
    """Delete any service request for this customer other than the canonical
    one, e.g. a row left over from an older version of this script that used
    a different service_request_id. Without this, get_latest_service_request
    (which picks the single row with the latest service_request_date) can
    keep surfacing a stale id forever, since _seed_service_request only ever
    looks up/inserts by the current canonical id and never touches rows
    under any other id.
    """
    stale = (
        session.query(ServiceRequest)
        .filter(
            ServiceRequest.customer_id == customer_id,
            ServiceRequest.service_request_id != canonical_service_request_id,
        )
        .all()
    )
    for stale_request in stale:
        session.delete(stale_request)
        result.service_requests_pruned += 1


def _seed_customer_360(
    session: Session,
    customer_id: int,
    account_number: str,
    service_request_id: int,
    next_best_offer_product_id: int,
    result: SeedResult,
) -> Customer360:
    snapshot = session.query(Customer360).filter_by(customer_id=customer_id).first()
    source = _CUSTOMER_360_SOURCE_JSON
    next_best_offer = source["next_best_offer"]

    if snapshot is not None:
        snapshot.account_number = account_number
        snapshot.customer_name = source["customer_name"]
        snapshot.onboarding_date = date.fromisoformat(source["onboarding_date"])
        snapshot.email_work = source["email_work"]
        snapshot.email_personal = source["email_personal"]
        snapshot.addresses_json = json.dumps(source["address"])
        snapshot.current_instruments_json = json.dumps(source["current_instruments"])
        snapshot.latest_service_request_id = service_request_id
        snapshot.next_best_offer_product_external_id = next_best_offer["recommended_product_id"]
        snapshot.next_best_offer_product_id = next_best_offer_product_id
        snapshot.next_best_offer_action_type = next_best_offer["action_type"]
        snapshot.next_best_offer_applicable_discounts = next_best_offer["applicable_discounts"]
        snapshot.next_best_offer_reason = next_best_offer["reason"]
        snapshot.raw_json = json.dumps(source)
        result.skipped += 1
        return snapshot

    snapshot = Customer360(
        customer_id=customer_id,
        account_number=account_number,
        customer_name=source["customer_name"],
        onboarding_date=date.fromisoformat(source["onboarding_date"]),
        email_work=source["email_work"],
        email_personal=source["email_personal"],
        addresses_json=json.dumps(source["address"]),
        current_instruments_json=json.dumps(source["current_instruments"]),
        latest_service_request_id=service_request_id,
        next_best_offer_product_external_id=next_best_offer["recommended_product_id"],
        next_best_offer_product_id=next_best_offer_product_id,
        next_best_offer_action_type=next_best_offer["action_type"],
        next_best_offer_applicable_discounts=next_best_offer["applicable_discounts"],
        next_best_offer_reason=next_best_offer["reason"],
        raw_json=json.dumps(source),
    )
    session.add(snapshot)
    session.flush()
    result.customer_360_inserted += 1
    return snapshot


def seed_demo_data(
    account_number: str = DEFAULT_DEMO_ACCOUNT_NUMBER,
    session_factory: sessionmaker | None = None,
    as_of: date | None = None,
) -> SeedResult:
    """Seed the "Digital RM Twin" demo dataset under account_number.

    Idempotent: re-running updates identity/catalogue rows in place (so a
    change to this file's source data is picked up by existing seeded rows
    too) and skips re-inserting anything matched by its natural key -- card
    product name/external id, card identity, merchant identity/alias, one
    customer per account, the service request's business id, and the
    transaction dedup key already enforced by the schema.
    """
    factory = session_factory or SessionLocal
    result = SeedResult()
    resolved_as_of = as_of or date.today()

    with factory() as session:
        _get_or_create_account(session, account_number)
        merchant = _seed_merchant(session, result)
        _seed_merchant_alias(session, merchant.id, WISDOM_PROPERTY_DESCRIPTOR, result)
        debit_product, credit_product = _seed_card_products(session, result)
        card = _seed_card(session, account_number, debit_product.id, result)
        customer = _seed_customer(session, account_number, result)
        _seed_transactions(session, account_number, card.id, merchant.id, result, resolved_as_of)
        service_request = _seed_service_request(session, customer.id, result)
        _seed_customer_360(session, customer.id, account_number, service_request.id, credit_product.id, result)
        _prune_stale_service_requests(session, customer.id, service_request.service_request_id, result)

        session.commit()

    logger.info(
        "seed_demo_data complete: merchants=%s aliases=%s card_products=%s cards=%s "
        "customers=%s transactions=%s transactions_pruned=%s service_requests=%s "
        "service_requests_pruned=%s customer_360=%s skipped=%s errors=%s",
        result.merchants_inserted,
        result.aliases_inserted,
        result.card_products_inserted,
        result.cards_inserted,
        result.customers_inserted,
        result.transactions_inserted,
        result.transactions_pruned,
        result.service_requests_inserted,
        result.service_requests_pruned,
        result.customer_360_inserted,
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
        f"Transactions pruned: {result.transactions_pruned}, "
        f"Service requests: {result.service_requests_inserted}, "
        f"Service requests pruned: {result.service_requests_pruned}, "
        f"Customer 360: {result.customer_360_inserted}, "
        f"Skipped: {result.skipped}, Errors: {len(result.errors)}"
    )
    for err in result.errors:
        print(" -", err)


if __name__ == "__main__":
    main()
