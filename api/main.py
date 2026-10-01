"""FastAPI application exposing getAccountTxnDetails as an HTTP endpoint.

Run locally with:
    uvicorn api.main:app --reload
Then browse http://127.0.0.1:8000/docs for interactive API docs.
"""
from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import date
from decimal import Decimal

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from sqlalchemy.orm import Session

from api.dependencies import get_db
from api.repository import (
    AccountNotFoundError,
    CardApplicationNotFoundError,
    CardNotFoundError,
    CardProductNotFoundError,
    CustomerNotFoundError,
    DisputeAlreadyOpenError,
    DisputeNotFoundError,
    DisputeNotOpenError,
    DuplicateApplicationError,
    InvalidDateRangeError,
    InvalidDeliveryAddressTypeError,
    NoEligibleCardProductError,
    TransactionNotFoundError,
    create_card_application,
    create_dispute,
    get_account_txn_details,
    get_card,
    get_card_application_status,
    get_card_category_breakdown,
    get_card_forex_summary,
    get_card_product,
    get_customer_by_account,
    list_card_products,
    list_cards_for_account,
    recommend_card_upgrade,
    search_card_transactions,
    update_customer_delivery_preference,
    withdraw_dispute,
)
from api.schemas import (
    CardApplicationCreate,
    CardApplicationOut,
    CardOut,
    CardProductOut,
    CardRecommendationOut,
    CategorySpendOut,
    CustomerOut,
    DeliveryPreferenceUpdate,
    DisputeCreate,
    DisputeOut,
    ForexSummaryOut,
    TransactionOut,
)
from config.logging_config import configure_logging
from db.session import init_db

configure_logging()

# Shared-secret bearer token for remote (e.g. EC2) exposure -- mirrors the
# same approach used for the MCP server (mcp_server/server.py). Defaults to
# the same token as the MCP server (MCP_BEARER_TOKEN) so one value in .env
# protects both services; set API_BEARER_TOKEN explicitly if you ever want
# them different. Unset entirely = no auth layer, which is fine for
# 127.0.0.1-only local dev but must be set before this is reachable from
# outside the box, since some of these routes can serve the real (not demo)
# account's data.
API_BEARER_TOKEN = os.environ.get("API_BEARER_TOKEN") or os.environ.get("MCP_BEARER_TOKEN")


def require_bearer_token(authorization: str | None = Header(default=None)) -> None:
    if API_BEARER_TOKEN is None:
        return
    if authorization != f"Bearer {API_BEARER_TOKEN}":
        raise HTTPException(status_code=401, detail="Unauthorized")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    init_db()
    yield


app = FastAPI(
    title="RMA Account Statement API",
    description="Read-only access to imported account statement transactions.",
    version="1.0.0",
    lifespan=lifespan,
    dependencies=[Depends(require_bearer_token)],
)


@app.get("/accounts/{account_number}/transactions", response_model=list[TransactionOut])
def read_account_transactions(
    account_number: str,
    from_date: date = Query(..., description="Start of the date range (inclusive), e.g. 2026-09-01"),
    to_date: date = Query(..., description="End of the date range (inclusive), e.g. 2026-09-30"),
    db: Session = Depends(get_db),
) -> list[TransactionOut]:
    try:
        transactions = get_account_txn_details(db, account_number, from_date, to_date)
    except AccountNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidDateRangeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return [TransactionOut.model_validate(txn) for txn in transactions]


@app.get("/accounts/{account_number}/cards", response_model=list[CardOut])
def read_account_cards(
    account_number: str,
    db: Session = Depends(get_db),
) -> list[CardOut]:
    try:
        cards = list_cards_for_account(db, account_number)
    except AccountNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return [CardOut.model_validate(card) for card in cards]


@app.get("/cards/{card_id}", response_model=CardOut)
def read_card(card_id: int, db: Session = Depends(get_db)) -> CardOut:
    try:
        card = get_card(db, card_id)
    except CardNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return CardOut.model_validate(card)


@app.get("/card-products", response_model=list[CardProductOut])
def read_card_products(
    card_type: str | None = Query(None, description="Filter by 'debit' or 'credit'"),
    active_only: bool = Query(True),
    db: Session = Depends(get_db),
) -> list[CardProductOut]:
    products = list_card_products(db, card_type=card_type, active_only=active_only)
    return [CardProductOut.model_validate(product) for product in products]


@app.get("/card-products/{card_product_id}", response_model=CardProductOut)
def read_card_product(card_product_id: int, db: Session = Depends(get_db)) -> CardProductOut:
    try:
        product = get_card_product(db, card_product_id)
    except CardProductNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return CardProductOut.model_validate(product)


@app.get("/cards/{card_id}/transactions", response_model=list[TransactionOut])
def read_card_transactions(
    card_id: int,
    from_date: date = Query(..., description="Start of the date range (inclusive)"),
    to_date: date = Query(..., description="End of the date range (inclusive)"),
    amount_min: Decimal | None = Query(None, description="Minimum amount (foreign-currency or INR)"),
    amount_max: Decimal | None = Query(None, description="Maximum amount (foreign-currency or INR)"),
    merchant_text: str | None = Query(None, description="Free-text merchant search, e.g. 'Wisdom Property'"),
    db: Session = Depends(get_db),
) -> list[TransactionOut]:
    try:
        transactions = search_card_transactions(
            db, card_id, from_date, to_date, amount_min=amount_min, amount_max=amount_max, merchant_text=merchant_text
        )
    except CardNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidDateRangeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return [TransactionOut.model_validate(txn) for txn in transactions]


@app.get("/cards/{card_id}/forex-summary", response_model=ForexSummaryOut)
def read_card_forex_summary(
    card_id: int,
    as_of_date: date | None = Query(None, description="Window end date; defaults to today"),
    db: Session = Depends(get_db),
) -> ForexSummaryOut:
    try:
        summary = get_card_forex_summary(db, card_id, as_of_date=as_of_date)
    except CardNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return ForexSummaryOut.model_validate(summary)


@app.get("/cards/{card_id}/category-breakdown", response_model=list[CategorySpendOut])
def read_card_category_breakdown(
    card_id: int,
    from_date: date = Query(..., description="Start of the date range (inclusive)"),
    to_date: date = Query(..., description="End of the date range (inclusive)"),
    db: Session = Depends(get_db),
) -> list[CategorySpendOut]:
    try:
        breakdown = get_card_category_breakdown(db, card_id, from_date, to_date)
    except CardNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidDateRangeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return [CategorySpendOut.model_validate(item) for item in breakdown]


@app.get("/cards/{card_id}/recommendation", response_model=CardRecommendationOut)
def read_card_recommendation(card_id: int, db: Session = Depends(get_db)) -> CardRecommendationOut:
    try:
        recommendation = recommend_card_upgrade(db, card_id)
    except CardNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except NoEligibleCardProductError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return CardRecommendationOut.model_validate(recommendation)


@app.post("/card-applications", response_model=CardApplicationOut, status_code=201)
def submit_card_application(body: CardApplicationCreate, db: Session = Depends(get_db)) -> CardApplicationOut:
    try:
        application = create_card_application(
            db, body.customer_id, body.card_product_id, delivery_address=body.delivery_address
        )
    except (CustomerNotFoundError, CardProductNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DuplicateApplicationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return CardApplicationOut.model_validate(application)


@app.get("/card-applications/{application_id}", response_model=CardApplicationOut)
def read_card_application(application_id: int, db: Session = Depends(get_db)) -> CardApplicationOut:
    try:
        application = get_card_application_status(db, application_id)
    except CardApplicationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return CardApplicationOut.model_validate(application)


@app.get("/accounts/{account_number}/customer", response_model=CustomerOut)
def read_account_customer(account_number: str, db: Session = Depends(get_db)) -> CustomerOut:
    try:
        customer = get_customer_by_account(db, account_number)
    except CustomerNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return CustomerOut.model_validate(customer)


@app.post("/transactions/{transaction_id}/disputes", response_model=DisputeOut, status_code=201)
def raise_dispute(
    transaction_id: int,
    body: DisputeCreate,
    db: Session = Depends(get_db),
) -> DisputeOut:
    try:
        dispute = create_dispute(db, transaction_id, body.reason)
    except TransactionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DisputeAlreadyOpenError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return DisputeOut.model_validate(dispute)


@app.post("/disputes/{dispute_id}/withdraw", response_model=DisputeOut)
def withdraw_dispute_route(dispute_id: int, db: Session = Depends(get_db)) -> DisputeOut:
    try:
        dispute = withdraw_dispute(db, dispute_id)
    except DisputeNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DisputeNotOpenError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return DisputeOut.model_validate(dispute)


@app.patch("/customers/{customer_id}/delivery-preference", response_model=CustomerOut)
def update_delivery_preference(
    customer_id: int,
    body: DeliveryPreferenceUpdate,
    db: Session = Depends(get_db),
) -> CustomerOut:
    try:
        customer = update_customer_delivery_preference(db, customer_id, body.preferred_delivery_address_type)
    except InvalidDeliveryAddressTypeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except CustomerNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return CustomerOut.model_validate(customer)
