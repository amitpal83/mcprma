"""SQLAlchemy ORM models for the account statement database.

Two tables:
  accounts     - one row per bank account (keyed by account_number).
  transactions - one row per statement line, FK'd to accounts.

Money columns use Numeric (fixed-point), never Float, to avoid rounding
drift on currency values.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Account(Base):
    __tablename__ = "accounts"

    account_number: Mapped[str] = mapped_column(String(34), primary_key=True)
    display_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    transactions: Mapped[list["Transaction"]] = relationship(
        back_populates="account", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Account {self.account_number}>"


class Transaction(Base):
    """A statement line.

    Two kinds of rows share this table: plain bank-narration transactions
    (UPI/IMPS/ACH, `card_id` NULL) and debit-card transactions (`card_id` set,
    plus the forex/merchant columns populated when applicable) -- a credit
    card, which settles separately rather than hitting this account
    directly, never gets rows here (see `CardApplication` instead).
    """

    __tablename__ = "transactions"
    __table_args__ = (
        # Guards against re-importing the same statement twice.
        UniqueConstraint("account_number", "reference_no", "txn_date", name="uq_txn_dedup"),
        Index("ix_txn_account_date", "account_number", "txn_date"),
        Index("ix_txn_card_date", "card_id", "txn_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    account_number: Mapped[str] = mapped_column(ForeignKey("accounts.account_number"), nullable=False)
    txn_date: Mapped[date] = mapped_column(Date, nullable=False)
    merchant: Mapped[str] = mapped_column(String(500), nullable=False)
    reference_no: Mapped[str | None] = mapped_column(String(50), nullable=True)
    txn_amount_INR: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    # --- Card / forex / merchant enrichment ---
    card_id: Mapped[int | None] = mapped_column(ForeignKey("cards.id"), nullable=True)
    card_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    txn_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    txn_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    exchange_rate: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)
    forex_markup_amount_INR: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    category: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # --- Statement-line detail sourced from richer transaction feeds ---
    parent_entity: Mapped[str | None] = mapped_column(String(120), nullable=True)
    instrument_mode: Mapped[str | None] = mapped_column(String(50), nullable=True)
    transaction_type: Mapped[str | None] = mapped_column(String(20), nullable=True)  # "domestic" | "international"

    account: Mapped[Account] = relationship(back_populates="transactions")
    card: Mapped["Card | None"] = relationship()

    def __repr__(self) -> str:
        return (
            f"<Transaction id={self.id} account={self.account_number} "
            f"date={self.txn_date} balance={self.closing_balance}>"
        )


class CardProduct(Base):
    """Card catalogue entry (one row per product, e.g. 'Global Elite zero forex markup credit card').

    Shared by debit and credit products. `reward_transfer_partners` is stored
    as a JSON-encoded string (SQLite has no native JSON type) — decode/encode
    at the repository boundary so callers only ever see a `list[str]`.
    """

    __tablename__ = "card_products"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    network: Mapped[str] = mapped_column(String(20), nullable=False)
    card_type: Mapped[str] = mapped_column(String(10), nullable=False)  # "debit" | "credit"
    forex_markup_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    joining_fee: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    annual_fee: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    lounge_visits_domestic_per_year: Mapped[int | None] = mapped_column(nullable=True)
    lounge_visits_international_per_year: Mapped[int | None] = mapped_column(nullable=True)
    reward_transfer_partners: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    # --- Product-catalogue enrichment (external catalogue id + json-sourced detail) ---
    external_product_id: Mapped[str | None] = mapped_column(String(50), nullable=True, unique=True)
    forex_enabled: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    product_rewards_enabled: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    key_features: Mapped[str | None] = mapped_column(Text, nullable=True)
    eligibility_criteria: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return f"<CardProduct id={self.id} name={self.name!r} type={self.card_type}>"


class Card(Base):
    """An issued card instance linked to an account.

    Only debit cards get transaction history in this system (see
    `Transaction.card_id`) — credit cards are represented purely via
    `CardApplication` until/unless a future step needs their own ledger.
    """

    __tablename__ = "cards"
    __table_args__ = (
        UniqueConstraint("account_number", "last4", "network", name="uq_card_identity"),
        Index("ix_cards_account_status", "account_number", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    account_number: Mapped[str] = mapped_column(ForeignKey("accounts.account_number"), nullable=False)
    card_product_id: Mapped[int] = mapped_column(ForeignKey("card_products.id"), nullable=False)
    last4: Mapped[str] = mapped_column(String(4), nullable=False)
    network: Mapped[str] = mapped_column(String(20), nullable=False)
    card_type: Mapped[str] = mapped_column(String(10), nullable=False)  # must match card_products.card_type
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    issued_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    expiry_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_kyc_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    account: Mapped[Account] = relationship()
    card_product: Mapped[CardProduct] = relationship()

    def __repr__(self) -> str:
        return f"<Card id={self.id} account={self.account_number} last4={self.last4}>"


class Merchant(Base):
    """Canonical merchant, resolved from one or more raw statement descriptors."""

    __tablename__ = "merchants"
    __table_args__ = (UniqueConstraint("brand_name", "city", name="uq_merchant_identity"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    brand_name: Mapped[str] = mapped_column(String(120), nullable=False)
    mcc: Mapped[str | None] = mapped_column(String(4), nullable=True)
    category: Mapped[str | None] = mapped_column(String(50), nullable=True)
    sub_category: Mapped[str | None] = mapped_column(String(50), nullable=True)
    associated_property: Mapped[str | None] = mapped_column(String(120), nullable=True)
    city: Mapped[str | None] = mapped_column(String(80), nullable=True)
    country: Mapped[str | None] = mapped_column(String(2), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    def __repr__(self) -> str:
        return f"<Merchant id={self.id} brand={self.brand_name!r}>"


class MerchantAlias(Base):
    """A raw statement descriptor (or normalized pattern) mapped to a canonical merchant."""

    __tablename__ = "merchant_aliases"
    __table_args__ = (Index("ix_alias_pattern", "raw_pattern"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    merchant_id: Mapped[int] = mapped_column(ForeignKey("merchants.id"), nullable=False)
    raw_pattern: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    match_type: Mapped[str] = mapped_column(String(20), nullable=False, default="exact")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    merchant: Mapped[Merchant] = relationship()

    def __repr__(self) -> str:
        return f"<MerchantAlias id={self.id} pattern={self.raw_pattern!r} -> merchant_id={self.merchant_id}>"


class Customer(Base):
    """Minimal RM-facing customer profile — one row per account, for this demo's scope."""

    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    account_number: Mapped[str] = mapped_column(
        ForeignKey("accounts.account_number"), nullable=False, unique=True
    )
    full_name: Mapped[str] = mapped_column(String(120), nullable=False)
    registered_email: Mapped[str] = mapped_column(String(120), nullable=False)
    alt_email: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # Kept alongside registered_email/alt_email (which stay in sync with these
    # for backward compatibility with mask_email/CustomerOut) so the source
    # json's own field names ("email_work"/"email_personal") are preserved too.
    email_work: Mapped[str | None] = mapped_column(String(120), nullable=True)
    email_personal: Mapped[str | None] = mapped_column(String(120), nullable=True)
    onboarding_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    relationship_tier: Mapped[str] = mapped_column(String(20), nullable=False, default="STANDARD")
    delivery_address_office: Mapped[str | None] = mapped_column(String(250), nullable=True)
    delivery_address_home: Mapped[str | None] = mapped_column(String(250), nullable=True)
    preferred_delivery_address_type: Mapped[str | None] = mapped_column(String(10), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    account: Mapped[Account] = relationship()

    def __repr__(self) -> str:
        return f"<Customer id={self.id} account={self.account_number} name={self.full_name!r}>"


class Dispute(Base):
    """A customer-raised dispute against a transaction (raised -> withdrawn)."""

    __tablename__ = "disputes"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    transaction_id: Mapped[int] = mapped_column(ForeignKey("transactions.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="OPEN")  # OPEN | WITHDRAWN
    reason: Mapped[str | None] = mapped_column(String(250), nullable=True)
    raised_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    transaction: Mapped[Transaction] = relationship()

    def __repr__(self) -> str:
        return f"<Dispute id={self.id} transaction_id={self.transaction_id} status={self.status}>"


class CardApplication(Base):
    """A submitted application for a card product (typically a credit card upgrade)."""

    __tablename__ = "card_applications"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), nullable=False)
    card_product_id: Mapped[int] = mapped_column(ForeignKey("card_products.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="SUBMITTED")
    applied_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    fee_charged: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    # Snapshot, not a live FK to Customer's address fields — later preference
    # changes must not rewrite what was actually submitted on this application.
    delivery_address: Mapped[str | None] = mapped_column(String(250), nullable=True)

    customer: Mapped[Customer] = relationship()
    card_product: Mapped[CardProduct] = relationship()

    def __repr__(self) -> str:
        return f"<CardApplication id={self.id} customer_id={self.customer_id} status={self.status}>"


class ServiceRequest(Base):
    """A customer-raised service request (e.g. a statement/document dispatch)."""

    __tablename__ = "service_requests"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), nullable=False)
    service_request_id: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    service_request_type: Mapped[str] = mapped_column(String(50), nullable=False)
    service_request_date: Mapped[date] = mapped_column(Date, nullable=False)
    service_request_status: Mapped[str] = mapped_column(String(30), nullable=False)
    service_request_details: Mapped[str | None] = mapped_column(Text, nullable=True)
    service_request_delivery_address_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    customer: Mapped[Customer] = relationship()

    def __repr__(self) -> str:
        return f"<ServiceRequest id={self.id} service_request_id={self.service_request_id!r} status={self.service_request_status}>"


class Customer360(Base):
    """Denormalized customer-360 snapshot: identity, addresses, current
    instruments, latest service request, and next-best-offer, in one place.

    This is a seeded snapshot (no triggers/views exist in this codebase) --
    it is not kept live-synchronized with `Customer`/`Card`/`ServiceRequest`
    after it is written. `raw_json` preserves the complete source payload
    verbatim, alongside queryable columns extracted from it.
    """

    __tablename__ = "customer_360"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), nullable=False, unique=True)
    account_number: Mapped[str] = mapped_column(ForeignKey("accounts.account_number"), nullable=False)
    customer_name: Mapped[str] = mapped_column(String(120), nullable=False)
    onboarding_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    email_work: Mapped[str | None] = mapped_column(String(120), nullable=True)
    email_personal: Mapped[str | None] = mapped_column(String(120), nullable=True)
    addresses_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    current_instruments_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    latest_service_request_id: Mapped[int | None] = mapped_column(ForeignKey("service_requests.id"), nullable=True)
    next_best_offer_product_external_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    next_best_offer_product_id: Mapped[int | None] = mapped_column(ForeignKey("card_products.id"), nullable=True)
    next_best_offer_action_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    next_best_offer_applicable_discounts: Mapped[str | None] = mapped_column(String(250), nullable=True)
    next_best_offer_reason: Mapped[str | None] = mapped_column(String(250), nullable=True)
    raw_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    customer: Mapped[Customer] = relationship()
    account: Mapped[Account] = relationship()
    latest_service_request: Mapped[ServiceRequest | None] = relationship()
    next_best_offer_product: Mapped[CardProduct | None] = relationship()

    def __repr__(self) -> str:
        return f"<Customer360 id={self.id} customer_id={self.customer_id} account={self.account_number}>"
