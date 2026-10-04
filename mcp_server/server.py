"""MCP server exposing the RMA "Digital RM Twin" account/card/customer tools
over HTTP/SSE: transactions, cards and the card-product catalogue, customer
profile and customer-360, service requests, disputes, card recommendations,
and card applications.

Policy note: MCP servers must be reviewed and approved by BCG IT/Security
(CT GenAI Workspace Squad) before being registered or pointed at real data
via a client such as PortKey. This module is for local build/test only
until that approval is confirmed.

Run locally with:
    python -m mcp_server.server
This starts an SSE endpoint at http://<host>:<port>/sse (defaults below,
overridable via the MCP_HOST / MCP_PORT environment variables). A running
server process does not pick up source changes on its own -- restart it
after editing this file, or a client's tool catalogue/docstrings will keep
showing the old, already-running version.
"""
from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from sqlalchemy.orm import sessionmaker
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from api.repository import (
    AccountNotFoundError,
    CardApplicationNotFoundError,
    CardNotFoundError,
    CardProductNotFoundError,
    Customer360NotFoundError,
    CustomerNotFoundError,
    DisputeAlreadyOpenError,
    DisputeNotFoundError,
    DisputeNotOpenError,
    DuplicateApplicationError,
    InvalidDateRangeError,
    InvalidDeliveryAddressTypeError,
    NoEligibleCardProductError,
    ServiceRequestNotFoundError,
    TransactionNotFoundError,
)
from api.repository import create_card_application as query_create_card_application
from api.repository import create_dispute as query_create_dispute
from api.repository import get_account_txn_details as query_account_txn_details
from api.repository import get_card_application_status as query_get_card_application_status
from api.repository import get_card_category_breakdown as query_get_card_category_breakdown
from api.repository import get_card_forex_summary as query_get_card_forex_summary
from api.repository import get_card_product as query_get_card_product
from api.repository import get_customer_360 as query_get_customer_360
from api.repository import get_customer_by_account as query_get_customer_by_account
from api.repository import get_latest_service_request as query_get_latest_service_request
from api.repository import list_card_products as query_list_card_products
from api.repository import list_cards_for_account as query_list_cards_for_account
from api.repository import recommend_card_upgrade as query_recommend_card_upgrade
from api.repository import search_card_transactions as query_search_card_transactions
from api.repository import update_customer_delivery_preference as query_update_customer_delivery_preference
from api.repository import withdraw_dispute as query_withdraw_dispute
from api.schemas import (
    CardApplicationOut,
    CardOut,
    CardProductOut,
    CardRecommendationOut,
    Customer360Out,
    CustomerOut,
    DisputeOut,
    ServiceRequestOut,
    TransactionOut,
)
from config.logging_config import configure_logging
from db.session import SessionLocal, init_db

configure_logging()
logger = logging.getLogger(__name__)

DEFAULT_HOST = os.environ.get("MCP_HOST", "127.0.0.1")
DEFAULT_PORT = int(os.environ.get("MCP_PORT", "8001"))
# Shared-secret bearer token for remote (e.g. EC2) exposure. Unset = no auth
# layer, which is fine for 127.0.0.1-only local dev but must be set before
# binding to a public interface.
BEARER_TOKEN = os.environ.get("MCP_BEARER_TOKEN")

mcp = MCPServer(name="rma-account-statements")


class BearerTokenMiddleware:
    """Pure-ASGI middleware requiring 'Authorization: Bearer <token>' on every
    HTTP request. Pure-ASGI (not Starlette's BaseHTTPMiddleware) deliberately
    -- it only inspects headers and never touches the request/response body,
    so it can't interfere with the SSE endpoint's long-lived streaming.
    """

    def __init__(self, app: ASGIApp, token: str) -> None:
        self.app = app
        self.token = token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope["headers"])
        auth_header = headers.get(b"authorization", b"").decode("latin-1")
        if auth_header != f"Bearer {self.token}":
            response = PlainTextResponse("Unauthorized", status_code=401)
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)


def _parse_iso_date(value: str, field_name: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        # ToolError = an anticipated failure: the client gets is_error=True
        # with this message, no traceback. A bare ValueError here would be
        # treated as a crash and hidden from the client.
        raise ToolError(f"{field_name} must be an ISO date (YYYY-MM-DD), got: {value!r}") from exc


def _parse_decimal(value: str, field_name: str) -> Decimal:
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise ToolError(f"{field_name} must be a decimal number, got: {value!r}") from exc


def fetch_account_transactions(
    account_number: str,
    from_date: str,
    to_date: str,
    session_factory: sessionmaker | None = None,
) -> list[dict]:
    """Core lookup backing the MCP tool below.

    Kept as a plain function (independent of the MCP transport) so it can be
    called directly from tests with an injected session_factory, the same
    pattern used in etl/excel_importer.py and api/repository.py.
    """
    factory = session_factory or SessionLocal
    parsed_from = _parse_iso_date(from_date, "from_date")
    parsed_to = _parse_iso_date(to_date, "to_date")

    with factory() as session:
        try:
            transactions = query_account_txn_details(session, account_number, parsed_from, parsed_to)
        except (AccountNotFoundError, InvalidDateRangeError) as exc:
            # Anticipated failures -> ToolError, so the client gets a clean
            # is_error=True result instead of an opaque "crash" response.
            # MCP tools have a single error channel, so there is no
            # HTTP-status-style distinction between 404 and 400 here.
            raise ToolError(str(exc)) from exc

        return [TransactionOut.model_validate(txn).model_dump(mode="json") for txn in transactions]


@mcp.tool()
def get_account_txn_details(account_number: str, from_date: str, to_date: str) -> list[dict]:
    """Get every statement line for an account within an inclusive date range.

    Includes both plain bank-narration rows (UPI/IMPS/ACH) and debit-card
    rows; a credit card never appears here (see create_card_application /
    get_card_application_status instead -- credit cards aren't tracked as a
    transaction ledger in this system).

    Args:
        account_number: The account number to look up, e.g. "8552".
        from_date: Start of the range (inclusive), ISO format "YYYY-MM-DD".
        to_date: End of the range (inclusive), ISO format "YYYY-MM-DD".

    Returns:
        A list of transaction objects ordered oldest-first, each with: id,
        account_number, txn_date, value_date, narration (raw statement
        descriptor), reference_no, withdrawal_amount/deposit_amount (INR),
        closing_balance (INR, running balance after this line), card_id
        (set only on debit-card rows), merchant_id (set only when the
        narration resolved to a canonical merchant), category, and a set of
        forex fields -- txn_currency, txn_amount, exchange_rate,
        forex_markup_pct, forex_markup_amount, gst_on_markup -- all null
        unless this was a foreign-currency card spend.

    Raises:
        ToolError: account_number doesn't exist; from_date is after to_date;
            or either date isn't a valid "YYYY-MM-DD" string.
    """
    logger.info("MCP tool call: get_account_txn_details(%s, %s, %s)", account_number, from_date, to_date)
    return fetch_account_transactions(account_number, from_date, to_date)


def fetch_cards_for_account(
    account_number: str,
    session_factory: sessionmaker | None = None,
) -> list[dict]:
    factory = session_factory or SessionLocal
    with factory() as session:
        try:
            cards = query_list_cards_for_account(session, account_number)
        except AccountNotFoundError as exc:
            raise ToolError(str(exc)) from exc

        return [CardOut.model_validate(card).model_dump(mode="json") for card in cards]


@mcp.tool()
def list_account_cards(account_number: str) -> list[dict]:
    """List all cards (debit and/or credit) linked to an account.

    Args:
        account_number: The account number to look up, e.g. "8552".

    Returns:
        A list of card objects, each with: id (the internal card id,
        returned for reference only -- every other tool is account-scoped
        and resolves its own cards internally), account_number,
        card_product_id (FK into the catalogue -- see get_card_product),
        last4, network (e.g. "Visa"), card_type ("debit"/"credit"), status
        (e.g. "active"), issued_at, created_at. Empty list if the account
        has no cards.

    Raises:
        ToolError: account_number doesn't exist.
    """
    logger.info("MCP tool call: list_account_cards(%s)", account_number)
    return fetch_cards_for_account(account_number)


def fetch_card_products(
    card_type: str | None = None,
    active_only: bool = True,
    session_factory: sessionmaker | None = None,
) -> list[dict]:
    factory = session_factory or SessionLocal
    with factory() as session:
        products = query_list_card_products(session, card_type=card_type, active_only=active_only)
        return [CardProductOut.model_validate(product).model_dump(mode="json") for product in products]


@mcp.tool()
def list_card_products(card_type: str | None = None, active_only: bool = True) -> list[dict]:
    """List the card product catalogue (what a customer could be issued or upgraded to).

    Args:
        card_type: Optional filter, "debit" or "credit". Omit for all types.
        active_only: If true (default), only currently offered products are returned.

    Returns:
        A list of card-product objects, each with: id, external_product_id
        (the catalogue source's own id, e.g. "prod-2", or null if this
        product predates the catalogue), name, network, card_type,
        forex_markup_pct, forex_enabled (whether it carries forex markup at
        all), joining_fee, annual_fee, lounge_visits_domestic_per_year /
        lounge_visits_international_per_year (null means unlimited, not
        "not applicable"), guest_visits_per_year, product_rewards_enabled,
        reward_transfer_partners (list of loyalty programs, [] if none),
        key_features (list of marketing bullet points), eligibility_criteria
        (list of requirements to qualify), min_relationship_tier_for_discount
        / relationship_discount_pct (a joining-fee discount a customer only
        gets if their relationship_tier -- see get_customer_profile -- meets
        this minimum; see get_account_recommendation for how that's applied),
        is_active, created_at.
    """
    logger.info("MCP tool call: list_card_products(%s, %s)", card_type, active_only)
    return fetch_card_products(card_type, active_only)


def fetch_card_product(card_product_id: int, session_factory: sessionmaker | None = None) -> dict:
    factory = session_factory or SessionLocal
    with factory() as session:
        try:
            product = query_get_card_product(session, card_product_id)
        except CardProductNotFoundError as exc:
            raise ToolError(str(exc)) from exc

        return CardProductOut.model_validate(product).model_dump(mode="json")


@mcp.tool()
def get_card_product(card_product_id: int) -> dict[str, Any]:
    """Get a single card product from the catalogue.

    Args:
        card_product_id: The product's internal id, e.g. 1. Get this from
            list_card_products, or from the recommended_product.id in
            get_account_recommendation.

    Returns:
        A card-product object -- see list_card_products for the full field
        list (external_product_id, fees, forex_markup_pct, lounge/guest
        visit allowances, reward_transfer_partners, key_features,
        eligibility_criteria, discount eligibility, is_active).

    Raises:
        ToolError: card_product_id doesn't exist.
    """
    logger.info("MCP tool call: get_card_product(%s)", card_product_id)
    return fetch_card_product(card_product_id)


def fetch_account_card_transactions(
    account_number: str,
    from_date: str,
    to_date: str,
    amount_min: str | None = None,
    amount_max: str | None = None,
    merchant_text: str | None = None,
    session_factory: sessionmaker | None = None,
) -> list[dict]:
    factory = session_factory or SessionLocal
    parsed_from = _parse_iso_date(from_date, "from_date")
    parsed_to = _parse_iso_date(to_date, "to_date")
    parsed_amount_min = _parse_decimal(amount_min, "amount_min") if amount_min is not None else None
    parsed_amount_max = _parse_decimal(amount_max, "amount_max") if amount_max is not None else None

    with factory() as session:
        try:
            cards = query_list_cards_for_account(session, account_number)
        except AccountNotFoundError as exc:
            raise ToolError(str(exc)) from exc

        transactions = []
        for card in cards:
            try:
                transactions.extend(
                    query_search_card_transactions(
                        session,
                        card.id,
                        parsed_from,
                        parsed_to,
                        amount_min=parsed_amount_min,
                        amount_max=parsed_amount_max,
                        merchant_text=merchant_text,
                    )
                )
            except InvalidDateRangeError as exc:
                raise ToolError(str(exc)) from exc

        transactions.sort(key=lambda txn: (txn.txn_date, txn.id))
        return [TransactionOut.model_validate(txn).model_dump(mode="json") for txn in transactions]


@mcp.tool()
def search_account_transactions(
    account_number: str,
    from_date: str,
    to_date: str,
    amount_min: str | None = None,
    amount_max: str | None = None,
    merchant_text: str | None = None,
) -> list[dict]:
    """Search an account's card transactions by date range, optional amount range, and merchant text.

    Searches across every card linked to the account (merged and re-sorted
    by date) -- you don't need to know a specific card_id. Use this instead
    of get_account_txn_details when you want to filter by amount or
    merchant; get_account_txn_details also includes plain bank-narration
    rows that aren't tied to any card, which this tool excludes.

    Args:
        account_number: The account number to look up, e.g. "8552".
        from_date: Start of the range (inclusive), ISO format "YYYY-MM-DD".
        to_date: End of the range (inclusive), ISO format "YYYY-MM-DD".
        amount_min: Optional minimum amount, as a decimal string, e.g. "340"
            -- matches either the original foreign-currency amount
            (txn_amount) or the INR-settled amount (withdrawal_amount), since
            you may not know which currency a remembered figure was in.
        amount_max: Optional maximum amount, e.g. "360".
        merchant_text: Optional free-text merchant search, e.g. "Wisdom
            Property". Tries to resolve to a canonical merchant first (so
            this matches even if the raw statement narration looks nothing
            like it, e.g. "WISDOM PROPERTY NL II"); falls back to a
            case-insensitive substring match against the raw narration if it
            can't be resolved.

    Returns:
        A list of transaction objects (same shape as get_account_txn_details),
        ordered oldest-first across all of the account's cards. Empty list
        if nothing matches, including if the account has no cards.

    Raises:
        ToolError: account_number doesn't exist; from_date is after to_date;
            or a date/amount argument isn't a valid "YYYY-MM-DD" date /
            decimal number string.
    """
    logger.info(
        "MCP tool call: search_account_transactions(%s, %s, %s, %s, %s, %r)",
        account_number, from_date, to_date, amount_min, amount_max, merchant_text,
    )
    return fetch_account_card_transactions(account_number, from_date, to_date, amount_min, amount_max, merchant_text)


def fetch_account_forex_summary(
    account_number: str,
    as_of_date: str | None = None,
    session_factory: sessionmaker | None = None,
) -> dict:
    factory = session_factory or SessionLocal
    parsed_as_of = _parse_iso_date(as_of_date, "as_of_date") if as_of_date is not None else None

    with factory() as session:
        try:
            cards = query_list_cards_for_account(session, account_number)
        except AccountNotFoundError as exc:
            raise ToolError(str(exc)) from exc

        summaries = [query_get_card_forex_summary(session, card.id, as_of_date=parsed_as_of) for card in cards]

        if not summaries:
            as_of = parsed_as_of or date.today()
            return {
                "account_number": account_number,
                "from_date": (as_of - timedelta(days=365)).isoformat(),
                "to_date": as_of.isoformat(),
                "total_forex_spend_inr": "0.00",
                "total_markup_amount": "0.00",
                "total_gst_amount": "0.00",
                "total_markup_and_gst": "0.00",
                "transaction_count": 0,
            }

        total_spend = sum((s.total_forex_spend_inr for s in summaries), Decimal("0"))
        total_markup = sum((s.total_markup_amount for s in summaries), Decimal("0"))
        total_gst = sum((s.total_gst_amount for s in summaries), Decimal("0"))
        return {
            "account_number": account_number,
            "from_date": summaries[0].from_date.isoformat(),
            "to_date": summaries[0].to_date.isoformat(),
            "total_forex_spend_inr": str(total_spend),
            "total_markup_amount": str(total_markup),
            "total_gst_amount": str(total_gst),
            "total_markup_and_gst": str(total_markup + total_gst),
            "transaction_count": sum(s.transaction_count for s in summaries),
        }


@mcp.tool()
def get_account_forex_summary(account_number: str, as_of_date: str | None = None) -> dict[str, Any]:
    """Summarize an account's foreign-currency spend over the trailing 365 days ending as_of_date.

    Aggregates across every card linked to the account. Only counts
    transactions that actually carried forex markup (i.e. had a
    txn_currency set) -- domestic spend is excluded entirely. This is the
    same spend figure get_account_recommendation projects savings from.

    Args:
        account_number: The account number to look up, e.g. "8552".
        as_of_date: Optional window end date, ISO format "YYYY-MM-DD".
            Defaults to today. The window is always exactly the 365 days
            ending on this date.

    Returns:
        A summary object: account_number, from_date/to_date (the actual
        window used), total_forex_spend_inr (sum of INR-settled amounts
        across all cards), total_markup_amount, total_gst_amount,
        total_markup_and_gst (sum of the two), transaction_count. All zero
        if the account has no cards.

    Raises:
        ToolError: account_number doesn't exist, or as_of_date isn't a valid
            "YYYY-MM-DD" string.
    """
    logger.info("MCP tool call: get_account_forex_summary(%s, %s)", account_number, as_of_date)
    return fetch_account_forex_summary(account_number, as_of_date)


def fetch_account_category_breakdown(
    account_number: str,
    from_date: str,
    to_date: str,
    session_factory: sessionmaker | None = None,
) -> list[dict]:
    factory = session_factory or SessionLocal
    parsed_from = _parse_iso_date(from_date, "from_date")
    parsed_to = _parse_iso_date(to_date, "to_date")

    with factory() as session:
        try:
            cards = query_list_cards_for_account(session, account_number)
        except AccountNotFoundError as exc:
            raise ToolError(str(exc)) from exc

        totals: dict[str | None, dict[str, Any]] = {}
        for card in cards:
            try:
                breakdown = query_get_card_category_breakdown(session, card.id, parsed_from, parsed_to)
            except InvalidDateRangeError as exc:
                raise ToolError(str(exc)) from exc

            for item in breakdown:
                bucket = totals.setdefault(item.category, {"total_amount": Decimal("0"), "transaction_count": 0})
                bucket["total_amount"] += item.total_amount
                bucket["transaction_count"] += item.transaction_count

        return [
            {
                "category": category,
                "total_amount": str(values["total_amount"]),
                "transaction_count": values["transaction_count"],
            }
            for category, values in totals.items()
        ]


@mcp.tool()
def get_account_category_breakdown(account_number: str, from_date: str, to_date: str) -> list[dict]:
    """Group an account's spend by category (e.g. Dining, Travel, Hotel, Shopping) over a date range.

    Aggregates across every card linked to the account. Only counts rows
    with a withdrawal_amount set (spend, not deposits), and only rows with a
    non-null category.

    Args:
        account_number: The account number to look up, e.g. "8552".
        from_date: Start of the range (inclusive), ISO format "YYYY-MM-DD".
        to_date: End of the range (inclusive), ISO format "YYYY-MM-DD".

    Returns:
        A list of objects, one per distinct category present in the range
        across all of the account's cards: category, total_amount (INR,
        summed), transaction_count. Empty list if there's no spend in the
        range, including if the account has no cards.

    Raises:
        ToolError: account_number doesn't exist, or from_date is after
            to_date, or either date isn't a valid "YYYY-MM-DD" string.
    """
    logger.info("MCP tool call: get_account_category_breakdown(%s, %s, %s)", account_number, from_date, to_date)
    return fetch_account_category_breakdown(account_number, from_date, to_date)


def fetch_customer_by_account(account_number: str, session_factory: sessionmaker | None = None) -> dict[str, Any]:
    factory = session_factory or SessionLocal
    with factory() as session:
        try:
            customer = query_get_customer_by_account(session, account_number)
        except CustomerNotFoundError as exc:
            raise ToolError(str(exc)) from exc

        return CustomerOut.model_validate(customer).model_dump(mode="json")


@mcp.tool()
def get_customer_profile(account_number: str) -> dict[str, Any]:
    """Get an account's RM-facing customer profile: identity, relationship tier, delivery address.

    For the fuller 360-degree view (addresses list, instruments, the
    standing next-best-offer), use get_customer_360 instead.

    Args:
        account_number: The account number to look up, e.g. "8552".

    Returns:
        A customer object: id (use this as customer_id elsewhere, e.g.
        update_customer_delivery_preference, create_card_application),
        account_number, full_name, relationship_tier (e.g. "STANDARD",
        "PRIORITY", "PREMIUM", "PRIVATE" -- determines eligibility for
        card-product discounts, see get_account_recommendation),
        registered_email_masked / alt_email_masked / email_work_masked /
        email_personal_masked (local part obscured, domain visible, e.g.
        "wor***@email.com" -- the raw email is never returned),
        onboarding_date, delivery_address_office, delivery_address_home,
        preferred_delivery_address_type ("OFFICE" or "HOME"), updated_at.

    Raises:
        ToolError: account_number has no customer profile.
    """
    logger.info("MCP tool call: get_customer_profile(%s)", account_number)
    return fetch_customer_by_account(account_number)


def fetch_customer_360(account_number: str, session_factory: sessionmaker | None = None) -> dict[str, Any]:
    factory = session_factory or SessionLocal
    with factory() as session:
        try:
            snapshot = query_get_customer_360(session, account_number)
        except Customer360NotFoundError as exc:
            raise ToolError(str(exc)) from exc

        return Customer360Out.model_validate(snapshot).model_dump(mode="json")


@mcp.tool()
def get_customer_360(account_number: str) -> dict[str, Any]:
    """Get an account's full customer-360 snapshot: identity, every known
    address, every current banking instrument, and the standing next-best-offer.

    This is a denormalized, point-in-time snapshot (not a live view) built
    from the customer's own profile plus whatever source data it was seeded
    from -- for the always-current RM-facing profile fields alone, use
    get_customer_profile; for a live-computed recommendation instead of the
    stored next_best_offer, use get_account_recommendation.

    Args:
        account_number: The account number to look up, e.g. "8552".

    Returns:
        A snapshot object: id, customer_id, account_number, customer_name,
        onboarding_date, email_work_masked / email_personal_masked (masked
        the same way as get_customer_profile), addresses (list of
        {address_type, address, preferred_flag}, e.g. "Bank Branch"/"home"),
        current_instruments (list of {instrument_type, instrument_name,
        instrument_identifier, instrument_expiry, instrument_last_kyc} --
        e.g. the customer's debit card and savings account), next_best_offer
        ({recommended_product_id (a catalogue external_product_id, e.g.
        "prod-2"), action_type (e.g. "cross_sell"), applicable_discounts
        (e.g. "25% on joining fee"), reason (e.g. "HIGH FOREX Spending")}),
        updated_at.

    Raises:
        ToolError: account_number has no customer-360 snapshot.
    """
    logger.info("MCP tool call: get_customer_360(%s)", account_number)
    return fetch_customer_360(account_number)


def fetch_latest_service_request(account_number: str, session_factory: sessionmaker | None = None) -> dict[str, Any]:
    factory = session_factory or SessionLocal
    with factory() as session:
        try:
            customer = query_get_customer_by_account(session, account_number)
            service_request = query_get_latest_service_request(session, customer.id)
        except (CustomerNotFoundError, ServiceRequestNotFoundError) as exc:
            raise ToolError(str(exc)) from exc

        return ServiceRequestOut.model_validate(service_request).model_dump(mode="json")


@mcp.tool()
def get_latest_service_request(account_number: str) -> dict[str, Any]:
    """Get an account's most recent service request (e.g. a statement/document dispatch, card replacement).

    "Most recent" means the single request with the latest
    service_request_date -- this does not return the full history.

    Args:
        account_number: The account number to look up, e.g. "8552".

    Returns:
        A service-request object: id, customer_id, service_request_id (the
        business-facing id, e.g. "SR1156788-20261001"), service_request_type
        (e.g. "account_statement"), service_request_date,
        service_request_status (e.g. "under progress", "closed"),
        service_request_details (free text, e.g. courier/delivery notes),
        service_request_delivery_address_type, created_at, updated_at.

    Raises:
        ToolError: account_number has no customer profile, or that customer
            has no service requests at all.
    """
    logger.info("MCP tool call: get_latest_service_request(%s)", account_number)
    return fetch_latest_service_request(account_number)


def fetch_update_customer_delivery_preference(
    customer_id: int,
    preferred_delivery_address_type: str,
    session_factory: sessionmaker | None = None,
) -> dict[str, Any]:
    factory = session_factory or SessionLocal
    with factory() as session:
        try:
            customer = query_update_customer_delivery_preference(
                session, customer_id, preferred_delivery_address_type
            )
        except (InvalidDeliveryAddressTypeError, CustomerNotFoundError) as exc:
            raise ToolError(str(exc)) from exc

        return CustomerOut.model_validate(customer).model_dump(mode="json")


@mcp.tool()
def update_customer_delivery_preference(customer_id: int, preferred_delivery_address_type: str) -> dict[str, Any]:
    """Update which address a customer prefers for card/document delivery. This writes to the database.

    Args:
        customer_id: The customer's internal id, e.g. 1. Get this from
            get_customer_profile's `id` field.
        preferred_delivery_address_type: Either "OFFICE" or "HOME" (any
            other value is rejected). This only changes the preference flag
            -- it does not change delivery_address_office/_home themselves.

    Returns:
        The updated customer object, same shape as get_customer_profile.

    Raises:
        ToolError: preferred_delivery_address_type isn't "OFFICE" or "HOME",
            or customer_id doesn't exist.
    """
    logger.info(
        "MCP tool call: update_customer_delivery_preference(%s, %s)",
        customer_id,
        preferred_delivery_address_type,
    )
    return fetch_update_customer_delivery_preference(customer_id, preferred_delivery_address_type)


def fetch_create_dispute(
    transaction_id: int,
    reason: str,
    session_factory: sessionmaker | None = None,
) -> dict[str, Any]:
    factory = session_factory or SessionLocal
    with factory() as session:
        try:
            dispute = query_create_dispute(session, transaction_id, reason)
        except (TransactionNotFoundError, DisputeAlreadyOpenError) as exc:
            raise ToolError(str(exc)) from exc

        return DisputeOut.model_validate(dispute).model_dump(mode="json")


@mcp.tool()
def create_dispute(transaction_id: int, reason: str) -> dict[str, Any]:
    """Raise a dispute against a transaction. This writes to the database.

    A transaction can only have one OPEN dispute at a time -- withdraw the
    existing one first (see withdraw_dispute) if you need to re-raise.

    Args:
        transaction_id: The transaction's internal id (the `id` field from
            get_account_txn_details / search_account_transactions), e.g. 1.
        reason: Why the customer is disputing it, e.g. "Unrecognized charge".

    Returns:
        The created dispute object: id, transaction_id, status ("OPEN"),
        reason, raised_at, resolved_at (null).

    Raises:
        ToolError: transaction_id doesn't exist, or it already has an OPEN
            dispute.
    """
    logger.info("MCP tool call: create_dispute(%s, %r)", transaction_id, reason)
    return fetch_create_dispute(transaction_id, reason)


def fetch_withdraw_dispute(dispute_id: int, session_factory: sessionmaker | None = None) -> dict[str, Any]:
    factory = session_factory or SessionLocal
    with factory() as session:
        try:
            dispute = query_withdraw_dispute(session, dispute_id)
        except (DisputeNotFoundError, DisputeNotOpenError) as exc:
            raise ToolError(str(exc)) from exc

        return DisputeOut.model_validate(dispute).model_dump(mode="json")


@mcp.tool()
def withdraw_dispute(dispute_id: int) -> dict[str, Any]:
    """Withdraw an open dispute (e.g. the customer recognizes the charge). This writes to the database.

    Args:
        dispute_id: The dispute's internal id (the `id` field returned by
            create_dispute), e.g. 1.

    Returns:
        The updated dispute object: id, transaction_id, status
        ("WITHDRAWN"), reason, raised_at, resolved_at (now set).

    Raises:
        ToolError: dispute_id doesn't exist, or it isn't currently OPEN
            (e.g. already withdrawn).
    """
    logger.info("MCP tool call: withdraw_dispute(%s)", dispute_id)
    return fetch_withdraw_dispute(dispute_id)


def fetch_account_recommendation(account_number: str, session_factory: sessionmaker | None = None) -> dict[str, Any]:
    factory = session_factory or SessionLocal
    with factory() as session:
        try:
            cards = query_list_cards_for_account(session, account_number)
        except AccountNotFoundError as exc:
            raise ToolError(str(exc)) from exc

        if not cards:
            raise ToolError(f"No card found for account: {account_number}")

        try:
            recommendation = query_recommend_card_upgrade(session, cards[0].id)
        except (CardNotFoundError, NoEligibleCardProductError) as exc:
            raise ToolError(str(exc)) from exc

        return CardRecommendationOut.model_validate(recommendation).model_dump(mode="json")


@mcp.tool()
def get_account_recommendation(account_number: str) -> dict[str, Any]:
    """Recommend a lower-forex-markup card upgrade for an account and project the annual savings.

    Resolves the account's card internally (its earliest-issued card, same
    one next_best_offer in get_customer_360 is scoped to -- next-best-offer
    is inherently a customer/account-level concept, not a per-card one).
    This is a live computation (recomputed from that card's own trailing
    365-day forex history every call), not the stored snapshot in
    get_customer_360's next_best_offer -- the two should normally agree but
    this one is always current. The candidate is the active credit product
    in the catalogue with the lowest forex_markup_pct strictly below the
    resolved card's own product (tie-broken by lowest annual_fee); if none
    beats it, there's nothing to recommend.

    Args:
        account_number: The account number to look up, e.g. "8552".

    Returns:
        A recommendation object: current_card_id (the card this was
        computed against), recommended_product (the full card-product
        object, see get_card_product), trailing_12mo_forex_spend_inr (the
        spend the projection is based on), current_annual_markup_and_gst
        (what the current card costs in forex markup + GST on that same
        spend), projected_annual_markup_and_gst (what the recommended
        product would cost on the same spend), projected_annual_savings
        (the difference), joining_fee, annual_fee (the recommended
        product's own, undiscounted), discount_pct_applied (null unless the
        customer's relationship_tier -- see get_customer_profile -- meets
        the product's discount-eligibility minimum),
        net_joining_fee_after_discount (joining fee after any discount, with
        GST added back), action_type ("cross_sell" when the recommended
        product is a different card_type than the current card, e.g. debit
        to credit; "upgrade" otherwise), reason (a short human-readable
        justification, e.g. "HIGH FOREX Spending: ..." above a spend
        threshold, else a lower-markup pitch), applicable_discounts
        (formatted text, e.g. "25% on joining fee", or null if no discount
        applies).

    Raises:
        ToolError: account_number doesn't exist or has no cards, or no
            active credit product in the catalogue has a lower forex markup
            than the resolved card's own product.
    """
    logger.info("MCP tool call: get_account_recommendation(%s)", account_number)
    return fetch_account_recommendation(account_number)


def fetch_create_card_application(
    customer_id: int,
    card_product_id: int,
    delivery_address: str | None = None,
    session_factory: sessionmaker | None = None,
) -> dict[str, Any]:
    factory = session_factory or SessionLocal
    with factory() as session:
        try:
            application = query_create_card_application(
                session, customer_id, card_product_id, delivery_address=delivery_address
            )
        except (CustomerNotFoundError, CardProductNotFoundError, DuplicateApplicationError) as exc:
            raise ToolError(str(exc)) from exc

        return CardApplicationOut.model_validate(application).model_dump(mode="json")


@mcp.tool()
def create_card_application(
    customer_id: int,
    card_product_id: int,
    delivery_address: str | None = None,
) -> dict[str, Any]:
    """Submit a card application for a customer (e.g. accepting a get_account_recommendation pitch). This writes to the database.

    A given customer can only have one SUBMITTED application per
    card_product at a time -- submitting again for the same pair raises an
    error rather than creating a duplicate. Any relationship-tier discount
    the product is eligible for (see get_account_recommendation) is computed
    and applied automatically; you don't pass it in.

    Args:
        customer_id: The customer's internal id, e.g. 1.
        card_product_id: The card product being applied for, e.g. 2. Get
            this from list_card_products or get_account_recommendation's
            recommended_product.id.
        delivery_address: Optional delivery address, e.g. the customer's
            office address. This is a point-in-time snapshot, independent of
            the customer's delivery_address_office/_home on file -- a later
            change to those does not retroactively change this.

    Returns:
        The created application object: id (use this as application_id in
        get_card_application_status), customer_id, card_product_id, status
        ("SUBMITTED"), applied_at, discount_pct_applied, fee_charged,
        delivery_address.

    Raises:
        ToolError: customer_id or card_product_id doesn't exist, or that
            customer already has a SUBMITTED application for that product.
    """
    logger.info(
        "MCP tool call: create_card_application(%s, %s, %r)",
        customer_id, card_product_id, delivery_address,
    )
    return fetch_create_card_application(customer_id, card_product_id, delivery_address)


def fetch_card_application_status(application_id: int, session_factory: sessionmaker | None = None) -> dict[str, Any]:
    factory = session_factory or SessionLocal
    with factory() as session:
        try:
            application = query_get_card_application_status(session, application_id)
        except CardApplicationNotFoundError as exc:
            raise ToolError(str(exc)) from exc

        return CardApplicationOut.model_validate(application).model_dump(mode="json")


@mcp.tool()
def get_card_application_status(application_id: int) -> dict[str, Any]:
    """Get a card application's current status.

    Args:
        application_id: The application's internal id (the `id` field
            returned by create_card_application), e.g. 1.

    Returns:
        An application object: id, customer_id, card_product_id, status
        (e.g. "SUBMITTED"), applied_at, discount_pct_applied, fee_charged,
        delivery_address (the snapshot taken at submission time).

    Raises:
        ToolError: application_id doesn't exist.
    """
    logger.info("MCP tool call: get_card_application_status(%s)", application_id)
    return fetch_card_application_status(application_id)


def build_asgi_app() -> ASGIApp:
    """The same Starlette app mcp.run(transport="sse", ...) builds internally
    (mcp.sse_app(...)), optionally wrapped in bearer-token auth. Split out
    from main() so tests can build and exercise it without starting uvicorn.
    """
    app: ASGIApp = mcp.sse_app(host=DEFAULT_HOST)
    if BEARER_TOKEN:
        app = BearerTokenMiddleware(app, token=BEARER_TOKEN)
    else:
        logger.warning(
            "MCP_BEARER_TOKEN is not set -- running WITHOUT auth. Do not bind "
            "to a public interface (MCP_HOST) in this state."
        )
    return app


def main() -> None:
    import uvicorn

    init_db()
    logger.info("Starting MCP server (SSE transport) on %s:%s", DEFAULT_HOST, DEFAULT_PORT)
    config = uvicorn.Config(build_asgi_app(), host=DEFAULT_HOST, port=DEFAULT_PORT, log_level="info")
    uvicorn.Server(config).run()


if __name__ == "__main__":
    main()
