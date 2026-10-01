"""Reference examples: one function per MCP tool exposed by this server.

Each function below is self-contained and copy-pasteable -- take the one you
need, drop it into your own code, and call it with an active `session`
(an mcp.ClientSession). The connection boilerplate at the bottom
(`connect()` / `main()`) shows how to get that session; you only need to
set that up once per process, then reuse the same session for every call.

Two gotchas in how results come back (confirmed against the real server):
  - A tool that returns a LIST (e.g. list_account_cards) wraps it:
        result.structured_content == {"result": [ ... ]}
  - A tool that returns a single OBJECT (e.g. get_card) does NOT wrap it:
        result.structured_content == { ...the object's fields... }
  This is an MCP SDK convention (list-shaped return types get wrapped under
  "result", object-shaped ones don't), not something specific to this
  server -- every function below already unwraps it correctly for you.

Setup:
    pip install mcp
    export MCP_SERVER_URL="http://<host>:<port>/sse"
    export MCP_BEARER_TOKEN="<the token>"
"""
from __future__ import annotations

import asyncio
import os

from mcp import ClientSession
from mcp.client.sse import sse_client


def _unwrap(result) -> dict | list | None:
    """Raises on an error result; otherwise returns the already-unwrapped value."""
    if result.is_error:
        message = result.content[0].text if result.content else "(no error message)"
        raise RuntimeError(f"Tool call failed: {message}")
    content = result.structured_content
    # list-returning tools wrap under {"result": [...]}; object-returning
    # tools don't wrap at all -- return whichever shape we actually got.
    if isinstance(content, dict) and list(content.keys()) == ["result"]:
        return content["result"]
    return content


# --- Chapter 1: bank-statement transactions ---------------------------------

async def get_account_txn_details(session: ClientSession, account_number: str, from_date: str, to_date: str):
    """List an account's transactions within an inclusive date range.
    from_date/to_date are ISO strings, e.g. "2026-01-01".
    """
    result = await session.call_tool(
        "get_account_txn_details",
        {"account_number": account_number, "from_date": from_date, "to_date": to_date},
    )
    return _unwrap(result)


# --- Card & card-product catalogue ------------------------------------------

async def list_account_cards(session: ClientSession, account_number: str):
    """List all cards linked to an account."""
    result = await session.call_tool("list_account_cards", {"account_number": account_number})
    return _unwrap(result)


async def get_card(session: ClientSession, card_id: int):
    """Get a single card by its internal id."""
    result = await session.call_tool("get_card", {"card_id": card_id})
    return _unwrap(result)


async def list_card_products(session: ClientSession, card_type: str | None = None, active_only: bool = True):
    """List the card product catalogue. card_type is optional: "debit" or "credit"."""
    args = {"active_only": active_only}
    if card_type is not None:
        args["card_type"] = card_type
    result = await session.call_tool("list_card_products", args)
    return _unwrap(result)


async def get_card_product(session: ClientSession, card_product_id: int):
    """Get a single card product from the catalogue."""
    result = await session.call_tool("get_card_product", {"card_product_id": card_product_id})
    return _unwrap(result)


# --- Card-transaction search, forex summary, category breakdown ------------

async def search_card_transactions(
    session: ClientSession,
    card_id: int,
    from_date: str,
    to_date: str,
    amount_min: str | None = None,
    amount_max: str | None = None,
    merchant_text: str | None = None,
):
    """Search a card's transactions. amount_min/amount_max are decimal
    strings (e.g. "340.00"); merchant_text is free text, e.g. "Hilton".
    """
    args = {"card_id": card_id, "from_date": from_date, "to_date": to_date}
    if amount_min is not None:
        args["amount_min"] = amount_min
    if amount_max is not None:
        args["amount_max"] = amount_max
    if merchant_text is not None:
        args["merchant_text"] = merchant_text
    result = await session.call_tool("search_card_transactions", args)
    return _unwrap(result)


async def get_card_forex_summary(session: ClientSession, card_id: int, as_of_date: str | None = None):
    """Trailing-365-day forex spend + markup/GST summary for a card.
    as_of_date is optional (defaults to today server-side).
    """
    args = {"card_id": card_id}
    if as_of_date is not None:
        args["as_of_date"] = as_of_date
    result = await session.call_tool("get_card_forex_summary", args)
    return _unwrap(result)


async def get_card_category_breakdown(session: ClientSession, card_id: int, from_date: str, to_date: str):
    """Spend grouped by category (Travel, Dining, ...) over a date range."""
    result = await session.call_tool(
        "get_card_category_breakdown", {"card_id": card_id, "from_date": from_date, "to_date": to_date}
    )
    return _unwrap(result)


# --- Customer profile --------------------------------------------------------

async def get_customer_profile(session: ClientSession, account_number: str):
    """RM-facing customer profile for an account (email comes back masked)."""
    result = await session.call_tool("get_customer_profile", {"account_number": account_number})
    return _unwrap(result)


async def update_customer_delivery_preference(
    session: ClientSession, customer_id: int, preferred_delivery_address_type: str
):
    """preferred_delivery_address_type must be "OFFICE" or "HOME"."""
    result = await session.call_tool(
        "update_customer_delivery_preference",
        {"customer_id": customer_id, "preferred_delivery_address_type": preferred_delivery_address_type},
    )
    return _unwrap(result)


# --- Disputes -----------------------------------------------------------------

async def create_dispute(session: ClientSession, transaction_id: int, reason: str):
    """Raise a dispute against a transaction. Fails if one is already OPEN
    for that transaction."""
    result = await session.call_tool("create_dispute", {"transaction_id": transaction_id, "reason": reason})
    return _unwrap(result)


async def withdraw_dispute(session: ClientSession, dispute_id: int):
    """Withdraw an OPEN dispute (e.g. the customer recognizes the charge)."""
    result = await session.call_tool("withdraw_dispute", {"dispute_id": dispute_id})
    return _unwrap(result)


# --- Recommendation -----------------------------------------------------------

async def get_card_recommendation(session: ClientSession, card_id: int):
    """Recommend a lower-forex-markup card upgrade + projected annual savings."""
    result = await session.call_tool("get_card_recommendation", {"card_id": card_id})
    return _unwrap(result)


# --- Card applications ---------------------------------------------------------

async def create_card_application(
    session: ClientSession, customer_id: int, card_product_id: int, delivery_address: str | None = None
):
    """Submit a card application. Fails if a SUBMITTED application already
    exists for this (customer_id, card_product_id) pair."""
    args = {"customer_id": customer_id, "card_product_id": card_product_id}
    if delivery_address is not None:
        args["delivery_address"] = delivery_address
    result = await session.call_tool("create_card_application", args)
    return _unwrap(result)


async def get_card_application_status(session: ClientSession, application_id: int):
    """Get a card application's current status."""
    result = await session.call_tool("get_card_application_status", {"application_id": application_id})
    return _unwrap(result)


# --- Tool discovery (what an agent reads to decide what/how to call) ---------

async def list_all_tools(session: ClientSession):
    """Returns the full tool catalogue: name, description, input schema."""
    tools = await session.list_tools()
    return [
        {"name": t.name, "description": t.description, "input_schema": t.input_schema}
        for t in tools.tools
    ]


# --- Connection boilerplate (set this up once, reuse `session` for every call) ---

def connect():
    """Returns an async context manager yielding (read, write) streams.

    Usage:
        async with connect() as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                cards = await list_account_cards(session, "ACC101")
    """
    url = os.environ["MCP_SERVER_URL"]       # e.g. "http://<host>:8001/sse"
    token = os.environ["MCP_BEARER_TOKEN"]
    return sse_client(url, headers={"Authorization": f"Bearer {token}"})


async def main() -> None:
    """Minimal usage demo -- replace the ids below with real ones from your server."""
    async with connect() as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            print(await list_all_tools(session))
            print(await list_account_cards(session, "ACC101"))
            print(await get_card_forex_summary(session, card_id=1))


if __name__ == "__main__":
    asyncio.run(main())
