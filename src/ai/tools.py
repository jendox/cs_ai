from typing import Any

from src.ai.amazon_mcp_client import AmazonMCPHttpClient
from src.ai.context import get_current_brand


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


async def get_catalog_item_attributes(
    asin: str,
    marketplace_id: str,
) -> dict[str, Any]:
    """
    Fetch normalized catalog attributes for a single ASIN in a given marketplace.

    INTERNAL HELPER, NOT EXPOSED AS AN LLM TOOL.

    This is a thin wrapper around the `get_catalog_item_attributes` tool exposed by
    the Amazon SP-API MCP server. Internally, the MCP server queries the
    `/catalog/2022-04-01/items/{asin}` endpoint with `includedData=attributes` and
    normalizes the result into a product structure.

    JSON shape
    ----------
    The response is a JSON object with keys such as:

    - "asin": str
    - "marketplace_id": str
    - "primary_sku": str | null
    - "all_skus": list[str]
    - "title": str | null
    - "brand": str | null
    - "manufacturer": str | null
    - "bullets": list[str] | null
    - "description": str | null
    - "unspsc_code": str | null
    - "browse_node_ids": list[str] | null
    - "flavor": str | null
    - "scent": str | null
    - "size": str | null
    - "number_of_items": int | null
    - "unit_count": float | null
    - "unit_count_type": str | null
    - "list_price": float | null
    - "list_price_currency": str | null
    - "dimensions": {
          "height_cm": float | null,
          "width_cm": float | null,
          "length_cm": float | null,
          "weight_g": float | null
      } | null
    - plus various optional health / nutrition / dosage fields,
      depending on the product category.

    Parameters
    ----------
    asin : str
        The ASIN of the product, for example "B000123456".
    marketplace_id : str
        The marketplace identifier, e.g. "A1F83G8C2ARO7P" or a short code
        used on the MCP side like "UK", "DE", etc.

    Returns
    -------
    dict
        A JSON object representing the normalized product attributes.
    """
    client = AmazonMCPHttpClient.get_initialized_instance()
    return await client.get_catalog_item_attributes(
        asin=asin,
        marketplace_id=marketplace_id,
    )


async def get_product_by_text(
    query: str,
    limit: int = 3,
) -> dict[str, Any]:
    """
    Search for one or more Amazon products for the CURRENT BRAND by free-text query.

    This is a thin wrapper around `AmazonMCPHttpClient.get_product_by_text(...)`
    which combines:

    - a full-text search over the internal `merchant_listings` table
      (restricted to the current brand), and
    - SP-API catalog lookups for each matched ASIN/marketplace pair.

    The current brand is taken implicitly from `llm_call_ctx.get().brand`, so the
    model MUST NOT pass any brand identifier explicitly.

    JSON shape
    ----------
    The returned object has a top-level `"status"` field and one of several
    payload variants:

    1) No match:

       {
         "status": "not_found",
         "candidates": []
       }

    2) Exactly one confident match:

       {
         "status": "resolved",
         "product": {
           "asin": str,
           "marketplace_id": str,
           "seller_sku": str | null,
           "title": str | null,
           "description": str | null,
           "rank": float,                 # text-search relevance score
           "product": Product-JSON
         }
       }

    3) Multiple plausible candidates:

       {
         "status": "multiple_candidates",
         "candidates": [
           {
             "asin": str,
             "marketplace_id": str,
             "seller_sku": str | null,
             "title": str | null,
             "description": str | null,
             "rank": float,
             "product": Product-JSON
           },
           ...
         ]
       }

    `Product-JSON` contains rich catalog attributes for the product, with keys such as:
    - "asin": str
    - "marketplace_id": str
    - "primary_sku": str | null
    - "all_skus": list[str]
    - "title": str | null
    - "brand": str | null
    - "manufacturer": str | null
    - "bullets": list[str] | null
    - "description": str | null
    - "unspsc_code": str | null
    - "browse_node_ids": list[str] | null
    - "flavor": str | null
    - "scent": str | null
    - "size": str | null
    - "number_of_items": int | null
    - "unit_count": float | null
    - "unit_count_type": str | null
    - "list_price": float | null
    - "list_price_currency": str | null
    - "dimensions": {
          "height_cm": float | null,
          "width_cm": float | null,
          "length_cm": float | null,
          "weight_g": float | null
      } | null
    - plus various optional health / nutrition / dosage fields
      depending on the product category.

    When to use this tool
    ---------------------
    Use this tool when:

    - The customer describes a product in words (name, flavour, size, etc.)
      but does NOT provide an ASIN or an order ID.
    - You need to identify which product they are talking about, limited to
      the current brand.
    - You want both:
        * basic listing info (title/description/SKU), and
        * rich catalog attributes (from SP-API).

    Parameters
    ----------
    query : str
        Free-text description of the product taken from the ticket or conversation.
        MUST NOT be empty or purely whitespace.
    limit : int, optional
        Maximum number of candidate products to fetch (default: 3). Larger values
        will increase latency and SP-API usage.

    Returns
    -------
    dict
        A JSON object with `"status"` and either `"product"` or `"candidates"` as
        described above.

    Tool behavior for the model
    ---------------------------
    - Prefer this tool when you have ONLY a textual description and no ASIN or
      order ID.
    - Do NOT invent ASINs or SKUs: always derive them from user text or other
      tools (orders, previous messages, etc.).
    - If `"status" == "multiple_candidates"`, you should:
        * inspect the candidates and pick the best match using the conversation
          context, or
        * ask the user for clarification if you cannot safely choose.
    """
    brand = get_current_brand(caller="get_product_by_text")
    client = AmazonMCPHttpClient.get_initialized_instance()
    return await client.get_product_by_text(query, brand.value, limit)


async def get_product_by_asin(
    asin: str,
    limit: int = 3,
) -> dict[str, Any]:
    """
    Fetch product information for a given ASIN across marketplaces for the CURRENT BRAND.

    This is a thin wrapper around `AmazonMCPHttpClient.get_product_by_asin(...)`.
    Internally it:

    1. Looks up all `merchant_listings` rows for (brand, asin).
    2. For up to `limit` marketplaces, calls the Amazon MCP server to get
       normalized catalog attributes for each ASIN/marketplace pair.

    The current brand is taken from `llm_call_ctx.get().brand`; the model MUST NOT
    pass any brand identifier directly.

    JSON shape
    ----------
    The returned object has a top-level `"status"` field and one of several
    payload variants:

    1) No listings for this ASIN within the current brand:

       {
         "status": "not_found",
         "variants": []
       }

    2) Exactly one variant (single marketplace / listing):

       {
         "status": "resolved",
         "product": {
           "asin": str,
           "marketplace_id": str,
           "seller_sku": str | null,
           "item_name": str | null,
           "item_description": str | null,
           "product": Product-JSON
         }
       }

    3) Multiple variants (same ASIN across several marketplaces / SKUs):

       {
         "status": "multiple_variants",
         "variants": [
           {
             "asin": str,
             "marketplace_id": str,
             "seller_sku": str | null,
             "item_name": str | null,
             "item_description": str | null,
             "product": Product-JSON
           },
           ...
         ]
       }

    `Product-JSON` contains rich catalog attributes for the product, with keys such as:
    - "asin": str
    - "marketplace_id": str
    - "primary_sku": str | null
    - "all_skus": list[str]
    - "title": str | null
    - "brand": str | null
    - "manufacturer": str | null
    - "bullets": list[str] | null
    - "description": str | null
    - "unspsc_code": str | null
    - "browse_node_ids": list[str] | null
    - "flavor": str | null
    - "scent": str | null
    - "size": str | null
    - "number_of_items": int | null
    - "unit_count": float | null
    - "unit_count_type": str | null
    - "list_price": float | null
    - "list_price_currency": str | null
    - "dimensions": {
          "height_cm": float | null,
          "width_cm": float | null,
          "length_cm": float | null,
          "weight_g": float | null
      } | null
    - plus various optional health / nutrition / dosage fields
      depending on the product category.

    When to use this tool
    ---------------------
    Use this tool when:

    - You already know the product's ASIN (e.g. from a link, previous message,
      or order item).
    - You want to retrieve all relevant listings for this ASIN within the
      current brand and marketplaces.
    - You need rich catalog attributes and/or need to choose the correct
      marketplace based on the conversation context.

    Parameters
    ----------
    asin : str
        The Amazon Standard Identification Number, for example "B000123456".
        The model MUST NOT invent ASINs; always use a value found in the
        user context or previous tool results.
    limit : int, optional
        Maximum number of marketplace variants to load (default: 3). Higher
        values increase latency and SP-API usage.

    Returns
    -------
    dict
        A JSON object with `"status"` and either `"product"` or `"variants"`
        as described above.

    Tool behavior for the model
    ---------------------------
    - Prefer this tool when you already have a concrete ASIN and need
      catalog information for that product.
    - If the result contains `"multiple_variants"`, use the conversation
      context (country, language, currency, marketplace) to select the
      most appropriate variant or to explain which variants are available.
    - If you do NOT have an ASIN but only free text, use `get_product_by_text`
      instead.
    """
    brand = get_current_brand(caller="get_product_by_asin")
    client = AmazonMCPHttpClient.get_initialized_instance()
    return await client.get_product_by_asin(asin, brand.value, limit)


async def get_products_by_order_id(
    order_id: str,
) -> dict[str, Any]:
    """
    Fetch an order together with fully enriched product information for each item.

    This is a higher-level convenience tool built on top of:

    - `get_full_order(order_id)` to retrieve the order summary and items, and
    - catalog lookups for each item (ASIN + MarketplaceId) via the MCP server.

    It is designed specifically for customer-service flows where you need to
    understand both the order and the products inside it.

    JSON shape
    ----------
    The returned object has two main keys:

    {
      "order":   <OrderSummary-JSON>,
      "products": [
        {
          "asin": str,
          "marketplace_id": str,
          "order_item": <OrderItem-JSON>,
          "product": Product-JSON
        },
        ...
      ]
    }

    - `order` is the same structure as in `get_order` / `get_full_order`
      (see their docstrings for the exact JSON shape).
    - Each `order_item` entry mirrors one element from the order's items list
      (quantity, price, title, etc.).
    - `product` is the rich catalog representation of that ASIN in the
      order's marketplace. It includes fields like asin, marketplace_id,
      title, brand, bullets, description, size, number_of_items, unit_count,
      list_price, dimensions, and other optional attributes (for example,
      health / nutrition / dosage information depending on the category).

    When to use this tool
    ---------------------
    Use this tool when:

    - The customer provides a valid Amazon order ID and you need to answer
      questions about specific products or items in that order.
    - You want to combine order-level information (status, totals, dates,
      shipping address) with detailed product attributes in a single call.
    - You are implementing flows like:
        * “Which exact product are they complaining about?”
        * “What is the size/flavour/count of the item in this order?”
        * “Is this the correct replacement for the item they bought?”

    Parameters
    ----------
    order_id : str
        The Amazon order identifier, for example "404-1234567-1234567".
        The model MUST NOT invent or guess order IDs; always extract them from
        the ticket or previous context.

    Returns
    -------
    dict
        A JSON object with `"order"` and `"products"` as described above.

    Tool behavior for the model
    ---------------------------
    - Prefer this tool whenever you have an order ID and need BOTH:
        * order-level details, and
        * product-level details for each item.
    - Use `get_full_order` if you only need information from the order and its
      items, but do NOT need catalog attributes.
    - Use `get_product_by_asin` or `get_product_by_text` for questions that are
      NOT tied to a specific order.
    """
    client = AmazonMCPHttpClient.get_initialized_instance()
    return await client.get_products_by_order_id(order_id)


amazon_tools = [
    get_order,
    get_order_items,
    get_full_order,
    get_product_by_text,
    get_product_by_asin,
    get_products_by_order_id,
]
