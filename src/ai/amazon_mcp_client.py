from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any, ClassVar, Self

import anyio
import fastmcp

ToolType = Callable[..., Awaitable[Any]]


class AmazonMCPHttpClientError(Exception): ...


class AmazonMCPHttpClient:
    """
    Singleton-style HTTP client for talking to the Amazon MCP server.

    This client manages a single underlying `fastmcp.Client` connection and exposes
    high-level async methods that can be used directly as tools for LLMs
    (e.g. in google.genai). Each public method has a detailed docstring that
    describes:

    - when to use the tool,
    - JSON shape of the response after `json.loads(...)`,
    - intended behavior and constraints for the model.
    """

    _initialized_instance: ClassVar[AmazonMCPHttpClient | None] = None

    def __init__(self, url: str) -> None:
        self.url = url
        self._client: fastmcp.Client | None = None
        self._lock = anyio.Lock()
        self.logger = logging.getLogger("amazon_mcp")

    # =========================
    #  Singleton / accessor
    # =========================

    @classmethod
    def get_initialized_instance(cls) -> Self:
        """
        Get the already initialized singleton instance.

        This is intended to be used only after the client has been created via
        the `setup()` context manager.

        Raises
        ------
        AmazonMCPHttpClientError
            If the client has not been created via `setup()`.
        """
        if initialized_instance := cls._initialized_instance:
            return initialized_instance
        raise AmazonMCPHttpClientError(
            "AmazonMCPHttpClient is not initialized. Use setup() context manager to "
            "initialize the client before calling get_initialized_instance().",
        )

    # ====================
    # Lifespan / context
    # ====================

    @classmethod
    @asynccontextmanager
    async def setup(cls, url: str) -> AsyncIterator[AmazonMCPHttpClient]:
        """
        Application-level lifespan context manager.

        This context manager:

        1. Creates and stores a singleton `AmazonMCPHttpClient` instance if it does
           not exist yet.
        2. Ensures the underlying MCP transport is opened.
        3. Yields the initialized client.
        4. Closes the transport and clears the singleton on exit.

        Example
        -------
        async with AmazonMCPHttpClient.setup("http://127.0.0.1:9002/mcp") as mcp_client:
            order = await mcp_client.get_full_order("404-1111111-2222222")
        """
        if cls._initialized_instance is None:
            cls._initialized_instance = cls(url)

        try:
            # ensure MCP transport is up
            await cls._initialized_instance._get_client()
            yield cls._initialized_instance
        finally:
            await cls._initialized_instance.close()
            cls._initialized_instance = None

    async def _get_client(self) -> fastmcp.Client:
        """
        Lazily create and enter the underlying fastmcp.Client under an async lock.

        Guarantees:
        - Only one Client is created per process.
        - `__aenter__()` is called exactly once.
        - Subsequent calls reuse the same open transport.
        """
        if self._client is not None:
            return self._client

        async with self._lock:
            if self._client is None:
                client = fastmcp.Client(self.url)
                # ruff: noqa: PLC2801
                await client.__aenter__()
                self._client = client
        return self._client

    async def close(self) -> None:
        """
        Gracefully shut down the underlying MCP client transport.
        """
        if self._client is not None:
            try:
                await self._client.__aexit__(None, None, None)
            finally:
                self._client = None

    # ======================
    #  MCP tools as methods
    # ======================

    async def get_order(self, order_id: str) -> dict[str, Any]:
        """
        Fetch a high-level summary of a single Amazon order from the MCP server.

        This is a thin wrapper around the `get_order` tool exposed by the Amazon
        SP-API MCP server. It returns a basic order summary without the detailed
        line items.

        JSON shape
        ----------
        The response is JSON-encoded `OrderSummary` and after `json.loads(...)`
        it will look like an object with (PascalCase) keys such as:

        - "AmazonOrderId": str
        - "SellerOrderId": str | null
        - "BuyerInfo": {
              "BuyerEmail": str | null
          } | null
        - "PurchaseDate": ISO-8601 datetime string
        - "LastUpdateDate": ISO-8601 datetime string
        - "OrderStatus": str
        - "FulfillmentChannel": str | null      # e.g. "AFN", "MFN"
        - "IsPrime": bool | null
        - "IsBusinessOrder": bool | null
        - "IsReplacementOrder": bool | null
        - "OrderTotal": {
              "Amount": string number,          # e.g. "12.99"
              "CurrencyCode": str              # e.g. "GBP"
          }
        - "NumberOfItemsShipped": int
        - "NumberOfItemsUnshipped": int
        - "ShipServiceLevel": str | null
        - "ShipmentServiceLevelCategory": str | null
        - "EarliestShipDate": ISO-8601 datetime string | null
        - "LatestShipDate": ISO-8601 datetime string | null
        - "SalesChannel": str | null
        - "MarketplaceId": str | null
        - "PaymentMethod": str | null
        - "PaymentMethodDetails": list[str] | null
        - "ShippingAddress": {
              "City": str | null
              "PostalCode": str | null
              "CountryCode": str | null
          } | null

        When to use this tool
        ---------------------
        Use this method when you need basic information about a specific Amazon
        order that the customer is asking about (status, shipping address,
        buyer info, order totals), but you do NOT need the detailed line
        items yet.

        Parameters
        ----------
        order_id : str
            The Amazon order identifier, for example "404-1234567-1234567".
            The model (or caller) should extract this value from the customer
            message or ticket. Do NOT invent or guess order IDs.

        Returns
        -------
        dict
            A JSON object representing `OrderSummary`.

        Tool behavior for the model
        ---------------------------
        - Call this tool when you need a quick overview of the order to
          understand status, totals, and basic customer information.
        - If you also need the list of products / SKUs in the order,
          prefer `get_full_order` instead of calling this tool and
          `get_order_items` separately.
        """
        self.logger.info("amazon_mcp.get_order", extra={"order_id": order_id})
        client = await self._get_client()
        result = await client.call_tool("get_order", {"order_id": order_id})
        return json.loads(result.content[0].text)

    async def get_order_items(self, order_id: str) -> dict[str, Any]:
        """
        Fetch the list of line items (products) for a single Amazon order.

        This is a thin wrapper around the `get_order_items` tool exposed by
        the Amazon SP-API MCP server. It returns the detailed list of items
        in the order.

        JSON shape
        ----------
        The response is JSON-encoded `OrderItemsSummary`. After `json.loads(...)`
        it will be an object with a single key:

        - "OrderItems": list[OrderItem]

        Each item in "OrderItems" has fields:
        - "OrderItemId": str
        - "ASIN": str
        - "SellerSKU": str
        - "Title": str | null
        - "QuantityOrdered": int
        - "QuantityShipped": int
        - "IsGift": bool | null
        - "ItemPrice": {
              "Amount": string number,
              "CurrencyCode": str
          }
        - "ItemTax": {
              "Amount": string number,
              "CurrencyCode": str
          } | null
        - "PromotionDiscount": {
              "Amount": string number,
              "CurrencyCode": str
          } | null
        - "PromotionDiscountTax": {
              "Amount": string number,
              "CurrencyCode": str
          } | null
        - "ProductInfo": {
              "NumberOfItems": int | null
          } | null

        When to use this tool
        ---------------------
        Use this method when you specifically need to know which products,
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
            A JSON object representing `OrderItemsSummary`.

        Tool behavior for the model
        ---------------------------
        - Use this tool when you already know the order ID and need
          item-level details only.
        - If you also need general order information (status, totals,
          shipping address), prefer `get_full_order` to get both in a
          single call.
        """
        self.logger.info("amazon_mcp.get_order_items", extra={"order_id": order_id})
        client = await self._get_client()
        result = await client.call_tool("get_order_items", {"order_id": order_id})
        return json.loads(result.content[0].text)

    async def get_full_order(self, order_id: str) -> dict[str, Any]:
        """
        Fetch both the order summary and its line items in a single call.

        This method calls the `get_full_order` MCP tool on the Amazon SP-API
        MCP server, which internally combines:

        - the order summary, and
        - the order items list

        JSON shape
        ----------
        The response is JSON-encoded `FullOrder`. After `json.loads(...)`
        it will be an object:

        {
          "Order": <OrderSummary JSON, как в get_order>,
          "Items": <OrderItemsSummary JSON, как в get_order_items>
        }

        When to use this tool
        ---------------------
        Use this method when you need complete context about an order to
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
            A JSON object representing `FullOrder` with two main keys:
            - "Order": the `OrderSummary` part
            - "Items": the `OrderItemsSummary` part

        Tool behavior for the model
        ---------------------------
        - Prefer this tool whenever you need both order-level and
          item-level information at the same time.
        - Use `get_order` only for quick status/summary checks.
        - Use `get_order_items` only when you already know the
          order and only need its line items.
        """
        self.logger.info("amazon_mcp.get_full_order", extra={"order_id": order_id})
        client = await self._get_client()
        result = await client.call_tool("get_full_order", {"order_id": order_id})
        return json.loads(result.content[0].text)

    async def get_merchant_listings_all_data(
        self,
        marketplace_id: str,
    ) -> list[dict[str, Any]]:
        """
        Fetch and parse the seller's catalog listings for a single marketplace.

        This is a wrapper around the `get_merchant_listings_all_data` tool
        exposed by the Amazon SP-API MCP server. Internally, the MCP server:

          1. Creates a `GET_MERCHANT_LISTINGS_ALL_DATA` report for the given
             marketplace.
          2. Waits for the report to finish.
          3. Downloads and parses the report (a TSV snapshot of all listings).
          4. Converts each valid row into a structured `MerchantListingRow`
             object.

        JSON shape
        ----------
        The response is a JSON array of `MerchantListingRow`. After
        `json.loads(...)` it will be:

        [
          {
            "asin": str,
            "seller_sku": str,
            "item_name": str | null,
            "item_description": str | null,
            "fulfillment_channel": str | null,  # e.g. "AMAZON_EU", "MFN"
            "status": str | null,               # e.g. "Active", "Inactive"
            "search_text": str | null           # usually "item_name\\n\\nitem_description"
          },
          ...
        ]

        When to use this tool
        ---------------------
        Use this method when you need a **searchable snapshot of the seller's
        active listings** for a specific marketplace, for example:

        - building or refreshing a product index / catalog,
        - powering retrieval-augmented generation (RAG) over the Amazon
          catalog,
        - answering questions like:
            * "Do we sell this product on Amazon UK?"
            * "What is the ASIN/SKU for this item?"
            * "Which listings mention this keyword in the title/description?"

        Parameters
        ----------
        marketplace_id : str
            The marketplace identifier to query. This should match the value
            used on the MCP side (e.g. an enum like "UK", "DE", "FR", or a
            raw SP-API marketplace ID such as "A1F83G8C2ARO7P").
            The model should NOT invent marketplace IDs and should use a
            value that is appropriate given the business context
            (e.g. defaulting to the main marketplace of this brand).

        Returns
        -------
        list[dict]
            A JSON array of `MerchantListingRow` objects.

        Tool behavior for the model
        ---------------------------
        - This tool can be relatively heavy, because it triggers the report
          generation flow on the Amazon SP-API. DO NOT call it repeatedly
          for every single user question.
        - Prefer to:
            * Call it once to build or refresh a catalog snapshot,
            * Cache or reuse its results at the application level.
        - Use it when:
            * You need to search across many of the seller's products,
            * You are implementing a retrieval step before answering complex
              catalog-related questions.
        - Avoid calling it when:
            * You only need information about a single order
              (use the order tools instead),
            * The question can be answered using already-known catalog data
              without regenerating the report.
        """
        self.logger.info(
            "amazon_mcp.get_merchant_listings_all_data",
            extra={"marketplace_id": marketplace_id},
        )
        client = await self._get_client()
        result = await client.call_tool(
            "get_merchant_listings_all_data",
            {"marketplace_id": marketplace_id},
        )
        return result.data

    @property
    def amazon_tools(self) -> list[ToolType]:
        return [
            self.get_order,
            self.get_order_items,
            self.get_full_order,
            self.get_merchant_listings_all_data,
        ]
