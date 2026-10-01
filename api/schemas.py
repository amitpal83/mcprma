"""Pydantic response schemas for the API layer.

Kept separate from db.models so the wire format (what a client sees) can
evolve independently of the ORM/table structure.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from api.repository.cards import decode_reward_transfer_partners
from api.repository.customers import mask_email


class TransactionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    account_number: str
    txn_date: date
    value_date: date
    narration: str
    reference_no: str | None
    withdrawal_amount: Decimal | None
    deposit_amount: Decimal | None
    closing_balance: Decimal
    created_at: datetime
    card_id: int | None = None
    merchant_id: int | None = None
    txn_currency: str | None = None
    txn_amount: Decimal | None = None
    exchange_rate: Decimal | None = None
    forex_markup_pct: Decimal | None = None
    forex_markup_amount: Decimal | None = None
    gst_on_markup: Decimal | None = None
    mcc: str | None = None
    category: str | None = None


class ForexSummaryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    card_id: int
    from_date: date
    to_date: date
    total_forex_spend_inr: Decimal
    total_markup_amount: Decimal
    total_gst_amount: Decimal
    total_markup_and_gst: Decimal
    transaction_count: int


class CategorySpendOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    category: str | None
    total_amount: Decimal
    transaction_count: int


class CardOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    account_number: str
    card_product_id: int
    last4: str
    network: str
    card_type: str
    status: str
    issued_at: date | None
    created_at: datetime


class CardProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    network: str
    card_type: str
    forex_markup_pct: Decimal
    joining_fee: Decimal
    annual_fee: Decimal
    lounge_visits_domestic_per_year: int | None
    lounge_visits_international_per_year: int | None
    guest_visits_per_year: int | None
    reward_transfer_partners: list[str]
    min_relationship_tier_for_discount: str | None
    relationship_discount_pct: Decimal | None
    is_active: bool
    created_at: datetime

    @field_validator("reward_transfer_partners", mode="before")
    @classmethod
    def _decode_reward_transfer_partners(cls, value: object) -> list[str]:
        if value is None or isinstance(value, str):
            return decode_reward_transfer_partners(value)
        return value


class CustomerOut(BaseModel):
    """Customer profile, wire-safe: the raw email is never a field here --
    only masked variants are, computed from the ORM row before validation.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    account_number: str
    full_name: str
    relationship_tier: str
    registered_email_masked: str
    alt_email_masked: str | None
    delivery_address_office: str | None
    delivery_address_home: str | None
    preferred_delivery_address_type: str | None
    updated_at: datetime

    @model_validator(mode="before")
    @classmethod
    def _mask_emails(cls, data: object) -> dict:
        if isinstance(data, dict):
            source = dict(data)
            registered_email = source.get("registered_email")
            alt_email = source.get("alt_email")
        else:
            source = {
                "id": data.id,
                "account_number": data.account_number,
                "full_name": data.full_name,
                "relationship_tier": data.relationship_tier,
                "delivery_address_office": data.delivery_address_office,
                "delivery_address_home": data.delivery_address_home,
                "preferred_delivery_address_type": data.preferred_delivery_address_type,
                "updated_at": data.updated_at,
            }
            registered_email = data.registered_email
            alt_email = data.alt_email

        source["registered_email_masked"] = mask_email(registered_email) if registered_email else None
        source["alt_email_masked"] = mask_email(alt_email) if alt_email else None
        return source


class DeliveryPreferenceUpdate(BaseModel):
    preferred_delivery_address_type: str


class DisputeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    transaction_id: int
    status: str
    reason: str | None
    raised_at: datetime
    resolved_at: datetime | None


class DisputeCreate(BaseModel):
    reason: str


class CardRecommendationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    current_card_id: int
    recommended_product: CardProductOut
    trailing_12mo_forex_spend_inr: Decimal
    current_annual_markup_and_gst: Decimal
    projected_annual_markup_and_gst: Decimal
    projected_annual_savings: Decimal
    joining_fee: Decimal
    annual_fee: Decimal
    discount_pct_applied: Decimal | None
    net_joining_fee_after_discount: Decimal


class CardApplicationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    customer_id: int
    card_product_id: int
    status: str
    applied_at: datetime
    discount_pct_applied: Decimal | None
    fee_charged: Decimal | None
    delivery_address: str | None


class CardApplicationCreate(BaseModel):
    customer_id: int
    card_product_id: int
    delivery_address: str | None = None
