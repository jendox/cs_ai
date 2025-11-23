import json
import logging
from typing import Any

from fastmcp import Client

AMAZON_TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_order",
        "description": "Get short summary of an Amazon order (no line items) by AmazonOrderId.",
        "parameters": {
            "type": "object",
            "properties": {
                "order_id": {
                    "type": "string",
                    "description": "AmazonOrderId, e.g. '206-5253111-7078766'.",
                },
            },
            "required": ["order_id"],
        },
    },
    {
        "name": "get_order_items",
        "description": "Get all line items (products) for a given Amazon order.",
        "parameters": {
            "type": "object",
            "properties": {
                "order_id": {
                    "type": "string",
                    "description": "AmazonOrderId, e.g. '206-5253111-7078766'.",
                },
            },
            "required": ["order_id"],
        },
    },
    {
        "name": "get_full_order",
        "description": "Get both order summary and line items of an Amazon order.",
        "parameters": {
            "type": "object",
            "properties": {
                "order_id": {
                    "type": "string",
                    "description": "AmazonOrderId, e.g. '206-5253111-7078766'.",
                },
            },
            "required": ["order_id"],
        },
    },
]

logger = logging.getLogger("amazon_tools")


async def async_get_order(order_id: str) -> dict[str, Any]:
    """
    Fetch a high-level summary of a single Amazon order from the MCP server.

    Use this tool when you need basic information about a specific Amazon
    order that the customer is asking about (status, shipping address,
    buyer info, order totals), but you do NOT need the detailed line
    items yet.

    Parameters
    ----------
    order_id : str
        The Amazon order identifier, for example "404-1234567-1234567".
        The model should extract this value from the customer message
        or ticket. Do NOT invent or guess order IDs.

    Returns
    -------
    dict
        A JSON object representing an `OrderSummary` structure produced
        by the Amazon SP-API MCP server. Typical fields include:
        - "AmazonOrderId"
        - "OrderStatus"
        - "OrderTotal" (amount + currency)
        - "PurchaseDate", "LastUpdateDate"
        - shipping address and buyer details (when available)

    Tool behavior for the model
    ---------------------------
    - Call this tool when you need a quick overview of the order
      to understand status, totals, and basic customer information.
    - If you also need the list of products / SKUs in the order,
      prefer `async_get_full_order` instead of calling this tool
      and `async_get_order_items` separately.
    """
    logger.info("async_get_order", extra={"order_id": order_id})
    async with Client("http://127.0.0.1:9002/mcp") as amazon_mcp_client:
        result = await amazon_mcp_client.call_tool("get_order", {"order_id": order_id})
        return json.loads(result.content[0].text)


async def async_get_order_items(order_id: str) -> dict[str, Any]:
    """
    Fetch the list of line items (products) for a single Amazon order.

    Use this tool when you specifically need to know which products,
    SKUs or ASINs are contained in an order, including quantities and
    per-item prices, to answer questions like:
    - "Which product is the customer complaining about?"
    - "How many units did they purchase?"
    - "What is the price of a specific line item?"

    Parameters
    ----------
    order_id : str
        The Amazon order identifier, for example "404-1234567-1234567".
        The model should NOT guess order IDs; always use the value
        provided in the input context (email, chat, ticket, etc.).

    Returns
    -------
    dict
        A JSON object representing an `OrderItemsSummary` structure
        produced by the Amazon SP-API MCP server. Typical fields
        include:
        - a list of items with:
          * "ASIN"
          * "SellerSKU"
          * "Title"
          * "QuantityOrdered"
          * "ItemPrice" and related tax/discount fields

    Tool behavior for the model
    ---------------------------
    - Use this tool when you already know the order ID and need
      item-level details only.
    - If you also need general order information (status, totals,
      shipping address), prefer `async_get_full_order` to get both
      in a single call.
    """
    logger.info("async_get_order_items", extra={"order_id": order_id})
    async with Client("http://127.0.0.1:9002/mcp") as amazon_mcp_client:
        result = await amazon_mcp_client.call_tool("get_order_items", {"order_id": order_id})
        return json.loads(result.content[0].text)


async def async_get_full_order(order_id: str) -> dict[str, Any]:
    """
    Fetch both the order summary and its line items in a single call.

    This tool calls the `get_full_order` MCP tool on the Amazon SP-API
    MCP server, which internally combines:
        - the order summary, and
        - the order items list

    Use this tool when you need complete context about an order to
    answer customer questions, for example:
    - refund / replacement decisions,
    - which items are affected,
    - checking totals, taxes and shipping details altogether.

    Parameters
    ----------
    order_id : str
        The Amazon order identifier, for example "404-1234567-1234567".
        The model should read this from the customer message instead
        of inventing an ID.

    Returns
    -------
    dict
        A JSON object representing a `FullOrder` structure with two
        main keys:
        - "order": the `OrderSummary` part
        - "items": the `OrderItemsSummary` part

    Tool behavior for the model
    ---------------------------
    - Prefer this tool whenever you need both order-level and
      item-level information at the same time.
    - Use `async_get_order` only for quick status/summary checks.
    - Use `async_get_order_items` only when you already know the
      order and only need its line items.
    """
    logger.info("async_get_full_order", extra={"order_id": order_id})
    async with Client("http://127.0.0.1:9002/mcp") as amazon_mcp_client:
        result = await amazon_mcp_client.call_tool("get_full_order", {"order_id": order_id})
        return json.loads(result.content[0].text)


amazon_mcp_tools = [
    async_get_order,
    async_get_order_items,
    async_get_full_order,
]
