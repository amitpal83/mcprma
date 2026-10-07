"""Pydantic response schemas for the API layer.

Kept separate from db.models so the wire format (what a client sees) can
evolve independently of the ORM/table structure.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from api.repository.cards import (
    decode_eligibility_criteria,
    decode_key_features,
    decode_reward_transfer_partners,
)
from api.repository.customers import mask_email


class TransactionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    account_number: str
    txn_date: date
    merchant: str
    reference_no: str | None
    txn_amount_INR: Decimal | None
    created_at: datetime
    card_id: int | None = None
    card_type: str | None = None
    txn_currency: str
    txn_amount: Decimal | None = None
    exchange_rate: Decimal | None = None
    forex_markup_amount_INR: Decimal | None = None
    category: str | None = None
    parent_entity: str | None = None
    instrument_mode: str | None = None
    transaction_type: str | None = None


class ForexSummaryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    card_id: int
    from_date: date
    to_date: date
    total_forex_spend_inr: Decimal
    total_markup_amount: Decimal
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
    reward_transfer_partners: list[str]
    is_active: bool
    created_at: datetime
    external_product_id: str | None = None
    forex_enabled: bool | None = None
    product_rewards_enabled: bool | None = None
    key_features: list[str] = []
    eligibility_criteria: list[str] = []

    @field_validator("reward_transfer_partners", mode="before")
    @classmethod
    def _decode_reward_transfer_partners(cls, value: object) -> list[str]:
        if value is None or isinstance(value, str):
            return decode_reward_transfer_partners(value)
        return value

    @field_validator("key_features", mode="before")
    @classmethod
    def _decode_key_features(cls, value: object) -> list[str]:
        if value is None or isinstance(value, str):
            return decode_key_features(value)
        return value

    @field_validator("eligibility_criteria", mode="before")
    @classmethod
    def _decode_eligibility_criteria(cls, value: object) -> list[str]:
        if value is None or isinstance(value, str):
            return decode_eligibility_criteria(value)
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
    email_work_masked: str | None
    email_personal_masked: str | None
    onboarding_date: date | None
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
            email_work = source.get("email_work")
            email_personal = source.get("email_personal")
        else:
            source = {
                "id": data.id,
                "account_number": data.account_number,
                "full_name": data.full_name,
                "relationship_tier": data.relationship_tier,
                "onboarding_date": data.onboarding_date,
                "delivery_address_office": data.delivery_address_office,
                "delivery_address_home": data.delivery_address_home,
                "preferred_delivery_address_type": data.preferred_delivery_address_type,
                "updated_at": data.updated_at,
            }
            registered_email = data.registered_email
            alt_email = data.alt_email
            email_work = data.email_work
            email_personal = data.email_personal

        source["registered_email_masked"] = mask_email(registered_email) if registered_email else None
        source["alt_email_masked"] = mask_email(alt_email) if alt_email else None
        source["email_work_masked"] = mask_email(email_work) if email_work else None
        source["email_personal_masked"] = mask_email(email_personal) if email_personal else None
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
    current_annual_markup: Decimal
    projected_annual_markup: Decimal
    projected_annual_savings: Decimal
    joining_fee: Decimal
    annual_fee: Decimal
    net_joining_fee: Decimal
    action_type: str
    reason: str


class ServiceRequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    customer_id: int
    service_request_id: str
    service_request_type: str
    service_request_date: date
    service_request_status: str
    service_request_details: str | None
    service_request_delivery_address_type: str | None
    created_at: datetime
    updated_at: datetime


class NextBestOfferOut(BaseModel):
    recommended_product_id: str | None
    action_type: str | None
    applicable_discounts: str | None
    reason: str | None


class Customer360Out(BaseModel):
    """Customer-360 snapshot, wire-safe: emails are masked like CustomerOut."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    customer_id: int
    account_number: str
    customer_name: str
    onboarding_date: date | None
    email_work_masked: str | None
    email_personal_masked: str | None
    addresses: list[dict]
    current_instruments: list[dict]
    next_best_offer: NextBestOfferOut
    updated_at: datetime

    @model_validator(mode="before")
    @classmethod
    def _shape(cls, data: object) -> dict:
        if isinstance(data, dict):
            source = dict(data)
            email_work = source.get("email_work")
            email_personal = source.get("email_personal")
            addresses_json = source.get("addresses_json")
            current_instruments_json = source.get("current_instruments_json")
        else:
            source = {
                "id": data.id,
                "customer_id": data.customer_id,
                "account_number": data.account_number,
                "customer_name": data.customer_name,
                "onboarding_date": data.onboarding_date,
                "updated_at": data.updated_at,
            }
            email_work = data.email_work
            email_personal = data.email_personal
            addresses_json = data.addresses_json
            current_instruments_json = data.current_instruments_json
            source["next_best_offer"] = {
                "recommended_product_id": data.next_best_offer_product_external_id,
                "action_type": data.next_best_offer_action_type,
                "applicable_discounts": data.next_best_offer_applicable_discounts,
                "reason": data.next_best_offer_reason,
            }

        source["email_work_masked"] = mask_email(email_work) if email_work else None
        source["email_personal_masked"] = mask_email(email_personal) if email_personal else None
        source["addresses"] = json.loads(addresses_json) if addresses_json else []
        source["current_instruments"] = json.loads(current_instruments_json) if current_instruments_json else []
        return source


class CardApplicationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    customer_id: int
    card_product_id: int
    status: str
    applied_at: datetime
    fee_charged: Decimal | None
    delivery_address: str | None


class CardApplicationCreate(BaseModel):
    customer_id: int
    card_product_id: int
    delivery_address: str | None = None
