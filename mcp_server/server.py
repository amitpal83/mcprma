"""MCP server exposing the RMA "Digital RM Twin" account/card/customer tools
over Streamable HTTP: transactions, cards and the card-product catalogue,
customer profile and customer-360, service requests, disputes, card
recommendations, and card applications.


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
from starlette.middleware.cors import CORSMiddleware
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
    InvalidAmountRangeError,
    InvalidDateRangeError,
    InvalidDeliveryAddressTypeError,
    InvalidTransactionTypeError,
    NoEligibleCardProductError,
    ServiceRequestNotFoundError,
    TransactionNotFoundError,
)
from api.repository import create_card_application as query_create_card_application
from api.repository import create_dispute as query_create_dispute
from api.repository import get_account_txn_details as query_account_txn_details
from api.repository import get_card_application_status as query_get_card_application_status
from api.repository import get_card_forex_summary as query_get_card_forex_summary
from api.repository import get_card_product as query_get_card_product
from api.repository import get_card_spend_summary as query_get_card_spend_summary
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
from mcp_server.offer_email import OfferEmailError, send_card_offer

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
    HTTP request. 
    -- it only inspects headers and never touches the request/response body,
    so it can't interfere with the Streamable HTTP endpoint's long-lived
    streaming responses.
    """

    def __init__(self, app: ASGIApp, token: str) -> None:
        self.app = app
        self.token = token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # CORS preflight requests never carry the app's own auth header (browsers
        # strip custom headers from an OPTIONS preflight) -- let it through
        # unauthenticated so CORSMiddleware (wrapped around this one) can answer
        # it. The real request that follows is still checked below as normal.
        if scope["method"] == "OPTIONS":
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
    called directly from tests with an injected session_factory.
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
def get_account_txn_details(account_number: str, from_date: str, to_date: str) -> dict[str, Any]:
    """Get all transactions for an account within an inclusive date range.

   
    Args:
        account_number: The account number to look up, e.g. "8552".
        from_date: Start of the range (inclusive), ISO format "YYYY-MM-DD".
        to_date: End of the range (inclusive), ISO format "YYYY-MM-DD".

    Returns:
        {"result": [...]} -- a list of transaction objects ordered
        oldest-first, each with: id, account_number, txn_date,
        merchant (raw statement merchant text), reference_no,
        txn_amount_INR , card_id, card_type ,
        txn_currency , category, parent_entity, instrument_mode,
        transaction_type ("domestic" or "international"),
        and a set of forex fields -- txn_amount, exchange_rate, forex_markup_amount_INR 
        `result` is `[]`, not an error, when nothing matches.

    Raises:
        ToolError: account_number doesn't exist; from_date is after to_date;
            or either date isn't a valid "YYYY-MM-DD" string.
    """
    logger.info("MCP tool call: get_account_txn_details(%s, %s, %s)", account_number, from_date, to_date)
    return {"result": fetch_account_transactions(account_number, from_date, to_date)}


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


# @mcp.tool()
def list_account_cards(account_number: str) -> dict[str, Any]:
    """List all cards (debit and/or credit) linked to an account.

    Args:
        account_number: The account number to look up, e.g. "8552".

    Returns:
        {"result": [...]} -- a list of card objects, each with: id (the
        internal card id, last4, network (e.g. "Visa"), card_type
        ("debit"/"credit"), status (e.g. "active"), issued_at, created_at.
        `result` is `[]`, not an error, if the account has no cards.

    Raises:
        ToolError: account_number doesn't exist.
    """
    logger.info("MCP tool call: list_account_cards(%s)", account_number)
    return {"result": fetch_cards_for_account(account_number)}


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
def list_card_products(card_type: str | None = None, active_only: bool = True) -> dict[str, Any]:
    """List the card product catalogue 

    Args:
        card_type: Optional filter, "debit" or "credit". Omit for all types.
        active_only: If true (default), only currently offered products are returned.

    Returns:
        {"result": [...]} -- a list of card-product objects, each with: id,
        card_type, forex_markup_pct, forex_enabled (whether it carries forex
        markup at all), joining_fee, annual_fee,
        lounge_visits_domestic_per_year / lounge_visits_international_per_year,
        product_rewards_enabled, reward_transfer_partners , key_features (list of marketing bullet
        points), relationship_discounts_appl , relationship_tier , eligibility_criteria (list of requirements to
        qualify, free text).
    """
    logger.info("MCP tool call: list_card_products(%s, %s)", card_type, active_only)
    return {"result": fetch_card_products(card_type, active_only)}



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
            except (InvalidDateRangeError, InvalidAmountRangeError) as exc:
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
) -> dict[str, Any]:
    """Search an account's card transactions by date range, optional amount range, and merchant text.

    Searches across every card linked to the account 

    Args:
        account_number: The account number to look up, e.g. "8552".
        from_date: Start of the range (inclusive), ISO format "YYYY-MM-DD".
        to_date: End of the range (inclusive), ISO format "YYYY-MM-DD".
        amount_min: Optional minimum amount, as a decimal string, e.g. "340"
        amount_max: Optional maximum amount, e.g. "360".
        merchant_text: Optional free-text merchant search, e.g. "Wisdom
            Property".
    Returns:
        a list of transaction objects ordered
        oldest-first, each with: id, account_number, txn_date,
        merchant (raw statement merchant text), reference_no,
        txn_amount_INR , card_id, card_type, 
        txn_currency, category, parent_entity (the outlet
        or property behind the merchant, e.g. the hotel "The Oberoi"),
        instrument_mode (the payment instrument used, e.g.
        "card-last4digits-4881"),
        transaction_type ("domestic" or "international"),
        and a set of forex fields like foreign-currency
        amount, exchange_rate, forex_markup_amount_INR 
        `result` is `[]`, not an error, when nothing
        matches (including when the account has no cards) -- an empty
        result means "no transactions found", not a failed search.

    Raises:
        ToolError: account_number doesn't exist; from_date is after to_date;
            amount_min is greater than amount_max; or a date/amount argument
            isn't a valid "YYYY-MM-DD" date / decimal number string.
    """
    logger.info(
        "MCP tool call: search_account_transactions(%s, %s, %s, %s, %s, %r)",
        account_number, from_date, to_date, amount_min, amount_max, merchant_text,
    )
    return {
        "result": fetch_account_card_transactions(
            account_number, from_date, to_date, amount_min, amount_max, merchant_text
        )
    }


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
                "transaction_count": 0,
            }

        total_spend = sum((s.total_forex_spend_inr for s in summaries), Decimal("0"))
        total_markup = sum((s.total_markup_amount for s in summaries), Decimal("0"))
        return {
            "account_number": account_number,
            "from_date": summaries[0].from_date.isoformat(),
            "to_date": summaries[0].to_date.isoformat(),
            "total_forex_spend_inr": str(total_spend),
            "total_markup_amount": str(total_markup),
            "transaction_count": sum(s.transaction_count for s in summaries),
        }


#@mcp.tool()
def get_account_forex_summary(account_number: str, as_of_date: str | None = None) -> dict[str, Any]:
    """Summarize an account's foreign-currency spend over the trailing 365 days ending as_of_date.

    Aggregates across every card linked to the account.

    Args:
        account_number: The account number to look up, e.g. "8552".
        as_of_date: Optional window end date, ISO format "YYYY-MM-DD".
            Defaults to today. The window is always exactly the 365 days
            ending on this date.

    Returns:
        A summary object: account_number, from_date/to_date (the actual
        window used), total_forex_spend_inr , total_markup_amount, transaction_count. All
        zero if the account has no cards.

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
    transaction_type: str,
    category: str | None = None,
    session_factory: sessionmaker | None = None,
) -> dict[str, Any]:
    factory = session_factory or SessionLocal
    parsed_from = _parse_iso_date(from_date, "from_date")
    parsed_to = _parse_iso_date(to_date, "to_date")

    with factory() as session:
        try:
            cards = query_list_cards_for_account(session, account_number)
        except AccountNotFoundError as exc:
            raise ToolError(str(exc)) from exc

        total_amount = Decimal("0")
        total_forex_markup = Decimal("0")
        transaction_count = 0
        for card in cards:
            try:
                summary = query_get_card_spend_summary(
                    session, card.id, parsed_from, parsed_to, transaction_type, category=category
                )
            except (InvalidDateRangeError, InvalidTransactionTypeError) as exc:
                raise ToolError(str(exc)) from exc

            total_amount += summary.total_amount
            total_forex_markup += summary.total_forex_markup_amount_INR
            transaction_count += summary.transaction_count

        return {
            "account_number": account_number,
            "from_date": from_date,
            "to_date": to_date,
            "transaction_type": transaction_type,
            "category": category,
            "total_amount": str(total_amount),
            "total_forex_markup_amount_INR": str(total_forex_markup),
            "transaction_count": transaction_count,
        }


@mcp.tool()
def get_transaction_category_analysis(
    account_number: str,
    from_date: str,
    to_date: str,
    transaction_type: str,
    category: str | None = None,
) -> dict[str, Any]:
    """Aggregate an account's spend (and forex markup) by date range,  transaction_type (domestic/international) , and  category.

    
    Args:
        account_number: The account number to look up, e.g. "8552".
        from_date: Start of the range (inclusive), ISO format "YYYY-MM-DD".
        to_date: End of the range (inclusive), ISO format "YYYY-MM-DD".
        transaction_type: Required. "domestic" or "international"
            (case-insensitive).
        category: Optional category filter, e.g. "Hotel". Omit for the
            total across every category.

    Returns:
        account_number, from_date, to_date, transaction_type, category ,
        total_amount, total_forex_markup_amount_INR , transaction_count. 
        All zero if the account has no cards or nothing matches.

    Raises:
        ToolError: account_number doesn't exist; from_date is after
            to_date; or transaction_type isn't "domestic" or
            "international".
    """
    logger.info(
        "MCP tool call: get_transaction_category_analysis(%s, %s, %s, %s, %s)",
        account_number, from_date, to_date, transaction_type, category,
    )
    return fetch_account_category_breakdown(account_number, from_date, to_date, transaction_type, category)


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
    """Get an account's  customer profile: identity, relationship tier, delivery address.

    

    Args:
        account_number: The account number to look up, e.g. "8552".

    Returns:
        A customer object: id ,
        registered_email, alt_email, email_work,
        email_personal, onboarding_date, delivery_address_office, delivery_address_home,
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
    """Get an account's full customer-360 snapshot: identity, relationship
    tier, home branch,  address, banking instrument.

    
    Args:
        account_number: The account number to look up, e.g. "8552".

    Returns:
        A Customer360 object: id, customer_id, account_number, customer_name,
        onboarding_date, email_work, email_personal, relationship_tier , home_branch ({name, address}), addresses (list of
        {address_type, address}, e.g. "Permanent"/"Correspondence"),
        current_instruments 
        e.g. the customer's debit card and savings account), updated_at.

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
    """Get an account's most recent service request 

    
    Args:
        account_number: The account number to look up, e.g. "8552".

    Returns:
        A service-request object: id, customer_id, service_request_id , service_request_type
        , service_request_date,
        service_request_status (e.g. "under progress", "closed"),
        service_request_details (free text, e.g. courier/delivery notes),
        service_request_delivery_address_type, created_at, updated_at.

    Raises:
        ToolError: account_number has no customer profile, or that customer
            has no service requests at all.
    """
    logger.info("MCP tool call: get_latest_service_request(%s)", account_number)
    return fetch_latest_service_request(account_number)




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
    """Raise a dispute against a transaction. 

    A transaction can only have one OPEN dispute at a time 
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


#@mcp.tool()
def get_account_recommendation(account_number: str) -> dict[str, Any]:
    """Recommend a lower-forex-markup card upgrade for an account and project the annual savings.

    Resolves the account's card internally (its earliest-issued card, same
    one the account's forex history is scoped to). This is a live computation
    (recomputed from that card's own trailing 365-day forex history every
    call). The candidate is the active credit product
    in the catalogue with the lowest forex_markup_pct strictly below the
    resolved card's own product (tie-broken by lowest annual_fee); if none
    beats it, there's nothing to recommend.

    Args:
        account_number: The account number to look up, e.g. "8552".

    Returns:
        A recommendation object: current_card_id (the card this was
        computed against), recommended_product (the full card-product
        object, see get_card_product), trailing_12mo_forex_spend_inr (the
        spend the projection is based on), current_annual_markup
        (what the current card costs in forex markup on that same
        spend), projected_annual_markup (what the recommended
        product would cost on the same spend), projected_annual_savings
        (the difference), joining_fee, annual_fee (the recommended
        product's own), net_joining_fee (joining_fee with GST added on
        top), action_type ("cross_sell" when the recommended product is a
        different card_type than the current card, e.g. debit to credit;
        "upgrade" otherwise), reason (a short human-readable justification,
        e.g. "HIGH FOREX Spending: ..." above a spend threshold, else a
        lower-markup pitch).

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
    """Submit a card application for a customer . 

    A given customer can only have one SUBMITTED application per
    card_product at a time -- submitting again for the same pair raises an
    error rather than creating a duplicate. 

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
        ("SUBMITTED"), applied_at, fee_charged, delivery_address.

    Raises:
        ToolError: customer_id or card_product_id doesn't exist, or that
            customer already has a SUBMITTED application for that product.
    """
    logger.info(
        "MCP tool call: create_card_application(%s, %s, %r)",
        customer_id, card_product_id, delivery_address,
    )
    return fetch_create_card_application(customer_id, card_product_id, delivery_address)


def fetch_send_card_offer_email(
    account_number: str,
    card_product_id: int,
    personal_note: str | None = None,
    session_factory: sessionmaker | None = None,
) -> dict[str, Any]:
    factory = session_factory or SessionLocal
    with factory() as session:
        try:
            snapshot = query_get_customer_360(session, account_number)
            product = query_get_card_product(session, card_product_id)
        except (Customer360NotFoundError, CardProductNotFoundError) as exc:
            raise ToolError(str(exc)) from exc

        if not product.is_active:
            raise ToolError(f"Card product {card_product_id} is not currently offered")
        recipient = snapshot.email_work or snapshot.email_personal
        if not recipient:
            raise ToolError(f"No email address on file for account {account_number}")

        try:
            return send_card_offer(
                customer_name=snapshot.customer_name,
                tier=snapshot.relationship_tier,
                recipient=recipient,
                product=CardProductOut.model_validate(product),
                personal_note=personal_note,
            )
        except OfferEmailError as exc:
            raise ToolError(str(exc)) from exc


@mcp.tool()
def send_card_offer_email(
    account_number: str, card_product_id: int, personal_note: str | None = None
) -> dict[str, Any]:
    """Email a card offer, with the product brochure attached, to the customer.

    This sends the email IMMEDIATELY -- there is no preview or approval step,
    so only call it when the RM has asked for the offer to go out.

    The recipient is the customer's own address on file (work email, else
    personal) -- it cannot be chosen by the caller. The email lists the
    product's key benefits, joining and annual fee, forex markup and
    eligibility, plus the customer's discount for that
    product .

    Args:
        account_number: The account number, e.g. "ACC101".
        card_product_id: The card product to offer, e.g. 2. Get this from
            list_card_products.
        personal_note: Optional short plain-text line from the RM, placed at
            the top of the email.

    Returns:
        status ("SENT"), sent_to (the address it was delivered to), subject,
        card_product_id, attachment (the PDF file name), discount_applied.

    Raises:
        ToolError: the account has no customer-360 snapshot or no email
            address; the card product doesn't exist or isn't offered; the
            brochure PDF is missing; email isn't configured on the server;
            or sending failed.
    """
    logger.info("MCP tool call: send_card_offer_email(%s, %s)", account_number, card_product_id)
    return fetch_send_card_offer_email(account_number, card_product_id, personal_note)


def build_asgi_app() -> ASGIApp:
    """The same app mcp.run(transport="streamable-http", ...) builds
    internally (mcp.streamable_http_app(...)),  wrapped in
    bearer-token auth, wrapped in CORS handling.

    CORSMiddleware has to be outermost: it must see (and short-circuit) an
    OPTIONS preflight itself, before BearerTokenMiddleware gets a chance to
    401 it -- browsers never send the Authorization header on a preflight,
    only on the real request that follows once the preflight succeeds.
    Without this, a browser-based client (e.g. a gateway dashboard like
    Portkey testing/discovering this server's tools from the user's browser)
    can never get past the preflight to make that real request at all.
    """
    app: ASGIApp = mcp.streamable_http_app(host=DEFAULT_HOST)
    if BEARER_TOKEN:
        app = BearerTokenMiddleware(app, token=BEARER_TOKEN)
    else:
        logger.warning(
            "MCP_BEARER_TOKEN is not set -- running WITHOUT auth. Do not bind "
            "to a public interface (MCP_HOST) in this state."
        )
    app = CORSMiddleware(
        app,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["Mcp-Session-Id"],
    )
    return app


def main() -> None:
    import uvicorn

    from etl.seed_demo_data import seed_demo_data

    init_db()
    seed_result = seed_demo_data()
    logger.info(
        "Demo data reconciled: transactions_inserted=%s transactions_pruned=%s "
        "service_requests_pruned=%s skipped=%s",
        seed_result.transactions_inserted,
        seed_result.transactions_pruned,
        seed_result.service_requests_pruned,
        seed_result.skipped,
    )
    logger.info("Starting MCP server (Streamable HTTP transport) on %s:%s", DEFAULT_HOST, DEFAULT_PORT)
    config = uvicorn.Config(build_asgi_app(), host=DEFAULT_HOST, port=DEFAULT_PORT, log_level="info")
    uvicorn.Server(config).run()


if __name__ == "__main__":
    main()
