from typing import Any

from src.ai.amazon_mcp_client import AmazonMCPHttpClient


async def get_order(order_id: str) -> dict[str, Any]:
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
    client = AmazonMCPHttpClient.get_initialized_instance()
    return await client.get_order(order_id)


async def get_order_items(order_id: str) -> dict[str, Any]:
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
    client = AmazonMCPHttpClient.get_initialized_instance()
    return await client.get_order_items(order_id)


async def get_full_order(order_id: str) -> dict[str, Any]:
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
    client = AmazonMCPHttpClient.get_initialized_instance()
    return await client.get_full_order(order_id)


async def get_merchant_listings_all_data(marketplace_id: str) -> list[dict[str, Any]]:
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
    client = AmazonMCPHttpClient.get_initialized_instance()
    return await client.get_merchant_listings_all_data(marketplace_id)


amazon_tools = [
    get_order,
    get_order_items,
    get_full_order,
    get_merchant_listings_all_data,
]
