"""MCP server exposing get_account_txn_details as a single tool over HTTP/SSE.

Policy note: MCP servers must be reviewed and approved by BCG IT/Security
(CT GenAI Workspace Squad) before being registered or pointed at real data
via a client such as PortKey. This module is for local build/test only
until that approval is confirmed.

Run locally with:
    python -m mcp_server.server
This starts an SSE endpoint at http://<host>:<port>/sse (defaults below,
overridable via the MCP_HOST / MCP_PORT environment variables).
"""
from __future__ import annotations

import logging
import os
from datetime import date, datetime
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
    CustomerNotFoundError,
    DisputeAlreadyOpenError,
    DisputeNotFoundError,
    DisputeNotOpenError,
    DuplicateApplicationError,
    InvalidDateRangeError,
    InvalidDeliveryAddressTypeError,
    NoEligibleCardProductError,
    TransactionNotFoundError,
)
from api.repository import create_card_application as query_create_card_application
from api.repository import create_dispute as query_create_dispute
from api.repository import get_account_txn_details as query_account_txn_details
from api.repository import get_card as query_get_card
from api.repository import get_card_application_status as query_get_card_application_status
from api.repository import get_card_category_breakdown as query_get_card_category_breakdown
from api.repository import get_card_forex_summary as query_get_card_forex_summary
from api.repository import get_card_product as query_get_card_product
from api.repository import get_customer_by_account as query_get_customer_by_account
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
    CategorySpendOut,
    CustomerOut,
    DisputeOut,
    ForexSummaryOut,
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
    """Get an account's transactions within an inclusive date range.

    Args:
        account_number: The account number to look up, e.g. "8552".
        from_date: Start of the range (inclusive), ISO format "YYYY-MM-DD".
        to_date: End of the range (inclusive), ISO format "YYYY-MM-DD".
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
    """List all cards linked to an account.

    Args:
        account_number: The account number to look up, e.g. "8552".
    """
    logger.info("MCP tool call: list_account_cards(%s)", account_number)
    return fetch_cards_for_account(account_number)


def fetch_card(card_id: int, session_factory: sessionmaker | None = None) -> dict:
    factory = session_factory or SessionLocal
    with factory() as session:
        try:
            card = query_get_card(session, card_id)
        except CardNotFoundError as exc:
            raise ToolError(str(exc)) from exc

        return CardOut.model_validate(card).model_dump(mode="json")


@mcp.tool()
def get_card(card_id: int) -> dict[str, Any]:
    """Get a single card by its id.

    Args:
        card_id: The card's internal id, e.g. 1.
    """
    logger.info("MCP tool call: get_card(%s)", card_id)
    return fetch_card(card_id)


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
    """List the card product catalogue.

    Args:
        card_type: Optional filter, "debit" or "credit". Omit for all types.
        active_only: If true (default), only currently offered products are returned.
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
        card_product_id: The product's internal id, e.g. 1.
    """
    logger.info("MCP tool call: get_card_product(%s)", card_product_id)
    return fetch_card_product(card_product_id)


def fetch_card_transactions(
    card_id: int,
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
            transactions = query_search_card_transactions(
                session,
                card_id,
                parsed_from,
                parsed_to,
                amount_min=parsed_amount_min,
                amount_max=parsed_amount_max,
                merchant_text=merchant_text,
            )
        except (CardNotFoundError, InvalidDateRangeError) as exc:
            raise ToolError(str(exc)) from exc

        return [TransactionOut.model_validate(txn).model_dump(mode="json") for txn in transactions]


@mcp.tool()
def search_card_transactions(
    card_id: int,
    from_date: str,
    to_date: str,
    amount_min: str | None = None,
    amount_max: str | None = None,
    merchant_text: str | None = None,
) -> list[dict]:
    """Search a card's transactions by date range, optional amount range, and merchant text.

    Args:
        card_id: The card's internal id, e.g. 1.
        from_date: Start of the range (inclusive), ISO format "YYYY-MM-DD".
        to_date: End of the range (inclusive), ISO format "YYYY-MM-DD".
        amount_min: Optional minimum amount, e.g. "340" -- matches either the
            original foreign-currency amount or the INR-settled amount.
        amount_max: Optional maximum amount, e.g. "360".
        merchant_text: Optional free-text merchant search, e.g. "Wisdom Property".
    """
    logger.info(
        "MCP tool call: search_card_transactions(%s, %s, %s, %s, %s, %r)",
        card_id, from_date, to_date, amount_min, amount_max, merchant_text,
    )
    return fetch_card_transactions(card_id, from_date, to_date, amount_min, amount_max, merchant_text)


def fetch_card_forex_summary(
    card_id: int,
    as_of_date: str | None = None,
    session_factory: sessionmaker | None = None,
) -> dict:
    factory = session_factory or SessionLocal
    parsed_as_of = _parse_iso_date(as_of_date, "as_of_date") if as_of_date is not None else None

    with factory() as session:
        try:
            summary = query_get_card_forex_summary(session, card_id, as_of_date=parsed_as_of)
        except CardNotFoundError as exc:
            raise ToolError(str(exc)) from exc

        return ForexSummaryOut.model_validate(summary).model_dump(mode="json")


@mcp.tool()
def get_card_forex_summary(card_id: int, as_of_date: str | None = None) -> dict[str, Any]:
    """Summarize a card's forex spend over the trailing 365 days ending as_of_date.

    Args:
        card_id: The card's internal id, e.g. 1.
        as_of_date: Optional window end date, ISO format "YYYY-MM-DD". Defaults to today.
    """
    logger.info("MCP tool call: get_card_forex_summary(%s, %s)", card_id, as_of_date)
    return fetch_card_forex_summary(card_id, as_of_date)


def fetch_card_category_breakdown(
    card_id: int,
    from_date: str,
    to_date: str,
    session_factory: sessionmaker | None = None,
) -> list[dict]:
    factory = session_factory or SessionLocal
    parsed_from = _parse_iso_date(from_date, "from_date")
    parsed_to = _parse_iso_date(to_date, "to_date")

    with factory() as session:
        try:
            breakdown = query_get_card_category_breakdown(session, card_id, parsed_from, parsed_to)
        except (CardNotFoundError, InvalidDateRangeError) as exc:
            raise ToolError(str(exc)) from exc

        return [CategorySpendOut.model_validate(item).model_dump(mode="json") for item in breakdown]


@mcp.tool()
def get_card_category_breakdown(card_id: int, from_date: str, to_date: str) -> list[dict]:
    """Group a card's spend by category over a date range.

    Args:
        card_id: The card's internal id, e.g. 1.
        from_date: Start of the range (inclusive), ISO format "YYYY-MM-DD".
        to_date: End of the range (inclusive), ISO format "YYYY-MM-DD".
    """
    logger.info("MCP tool call: get_card_category_breakdown(%s, %s, %s)", card_id, from_date, to_date)
    return fetch_card_category_breakdown(card_id, from_date, to_date)


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
    """Get an account's RM-facing customer profile (email is masked).

    Args:
        account_number: The account number to look up, e.g. "8552".
    """
    logger.info("MCP tool call: get_customer_profile(%s)", account_number)
    return fetch_customer_by_account(account_number)


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
    """Update which address a customer prefers for card/document delivery.

    Args:
        customer_id: The customer's internal id, e.g. 1.
        preferred_delivery_address_type: Either "OFFICE" or "HOME".
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
    """Raise a dispute against a transaction.

    Args:
        transaction_id: The transaction's internal id, e.g. 1.
        reason: Why the customer is disputing it, e.g. "Unrecognized charge".
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
    """Withdraw an open dispute (e.g. the customer recognizes the charge).

    Args:
        dispute_id: The dispute's internal id, e.g. 1.
    """
    logger.info("MCP tool call: withdraw_dispute(%s)", dispute_id)
    return fetch_withdraw_dispute(dispute_id)


def fetch_card_recommendation(card_id: int, session_factory: sessionmaker | None = None) -> dict[str, Any]:
    factory = session_factory or SessionLocal
    with factory() as session:
        try:
            recommendation = query_recommend_card_upgrade(session, card_id)
        except (CardNotFoundError, NoEligibleCardProductError) as exc:
            raise ToolError(str(exc)) from exc

        return CardRecommendationOut.model_validate(recommendation).model_dump(mode="json")


@mcp.tool()
def get_card_recommendation(card_id: int) -> dict[str, Any]:
    """Recommend a lower-forex-markup card upgrade and project the annual savings.

    Args:
        card_id: The current card's internal id, e.g. 1.
    """
    logger.info("MCP tool call: get_card_recommendation(%s)", card_id)
    return fetch_card_recommendation(card_id)


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
    """Submit a card application for a customer.

    Args:
        customer_id: The customer's internal id, e.g. 1.
        card_product_id: The card product being applied for, e.g. 2.
        delivery_address: Optional delivery address, e.g. the customer's office address.
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
        application_id: The application's internal id, e.g. 1.
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
