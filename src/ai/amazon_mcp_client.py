from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, ClassVar, Self

import anyio
import fastmcp

from src.db import session_local
from src.db.repositories.merchant_listing import MerchantListingRepository


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
        self.logger = logging.getLogger("amazon_mcp_client")

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
        self.logger.info("amazon_mcp.get_order", extra={"order_id": order_id})
        client = await self._get_client()
        result = await client.call_tool("get_order", {"order_id": order_id})
        content = result.structured_content
        self.logger.info(
            "get_order.success",
            extra={"content": json.dumps(content, ensure_ascii=False)},
        )
        return content

    async def get_order_items(self, order_id: str) -> dict[str, Any]:
        self.logger.info("amazon_mcp.get_order_items", extra={"order_id": order_id})
        client = await self._get_client()
        result = await client.call_tool("get_order_items", {"order_id": order_id})
        content = result.structured_content
        self.logger.info(
            "get_order_items.success",
            extra={"content": json.dumps(content, ensure_ascii=False)},
        )
        return content

    async def get_full_order(self, order_id: str) -> dict[str, Any]:
        self.logger.info("amazon_mcp.get_full_order", extra={"order_id": order_id})
        client = await self._get_client()
        result = await client.call_tool("get_full_order", {"order_id": order_id})
        content = result.structured_content
        self.logger.info(
            "get_full_order.success",
            extra={"content": json.dumps(content, ensure_ascii=False)},
        )
        return content

    async def get_merchant_listings_all_data(
        self,
        marketplace_id: str,
    ) -> list[dict[str, Any]]:
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

    async def get_catalog_item_attributes(
        self,
        asin: str,
        marketplace_id: str,
    ) -> dict[str, Any]:
        self.logger.info(
            "amazon_mcp.get_catalog_item_attributes",
            extra={"asin": asin, "marketplace_id": marketplace_id},
        )
        client = await self._get_client()
        result = await client.call_tool(
            "get_catalog_item_attributes",
            {"asin": asin, "marketplace_id": marketplace_id},
        )
        content = result.structured_content
        self.logger.info(
            "get_catalog_item_attributes.success",
            extra={"content": json.dumps(content, ensure_ascii=False)},
        )
        return content

    async def get_product_by_text(
        self,
        query: str,
        brand_id: int,
        limit: int = 5,
    ) -> dict[str, Any]:
        extra = {"query": query, "brand_id": brand_id}
        query = (query or "").strip()
        if not query:
            self.logger.debug("get_product_by_text.not_found", extra=extra)
            return {"status": "not_found", "candidates": []}

        async with session_local() as session:
            repo = MerchantListingRepository(session)
            rows = await repo.search_by_text(
                brand_id=brand_id,
                query=query,
                limit=limit,
            )
        if not rows:
            self.logger.debug("get_product_by_text.not_found", extra=extra)
            return {"status": "not_found", "candidates": []}

        candidates: list[dict[str, Any]] = []

        for entity, rank in rows:
            product = await self.get_catalog_item_attributes(entity.asin, entity.marketplace_id)
            candidates.append({
                "asin": entity.asin,
                "marketplace_id": entity.marketplace_id,
                "seller_sku": entity.seller_sku,
                "title": entity.item_name,
                "description": entity.item_description,
                "rank": rank,
                "product": product,
            })
        self.logger.debug(
            "get_product_by_text.success",
            extra={**extra, "products": candidates},
        )

        if len(candidates) == 1:
            return {"status": "resolved", "product": candidates[0]}

        return {"status": "multiple_candidates", "candidates": candidates}

    async def get_product_by_asin(
        self,
        asin: str,
        brand_id: int,
        limit: int = 3,
    ) -> dict[str, Any]:
        extra = {"asin": asin, "brand_id": brand_id}
        async with session_local() as session:
            repo = MerchantListingRepository(session)
            listings = await repo.search_by_asin(
                brand_id=brand_id,
                asin=asin,
            )
        if not listings:
            self.logger.debug("get_product_by_asin.not_found", extra=extra)
            return {"status": "not_found", "variants": []}

        variants: list[dict[str, Any]] = []

        for entity in listings[:limit]:
            product = await self.get_catalog_item_attributes(entity.asin, entity.marketplace_id)
            variants.append({
                "asin": entity.asin,
                "marketplace_id": entity.marketplace_id,
                "seller_sku": entity.seller_sku,
                "item_name": entity.item_name,
                "item_description": entity.item_description,
                "product": product,
            })
        self.logger.debug(
            "get_product_by_asin.success",
            extra={**extra, "products": variants},
        )

        if len(variants) == 1:
            return {"status": "resolved", "product": variants[0]}

        return {"status": "multiple_variants", "variants": variants}

    async def get_products_by_order_id(
        self,
        order_id: str,
    ) -> dict[str, Any]:
        full_order = await self.get_full_order(order_id)
        if not full_order:
            self.logger.debug("get_products_by_order_id.not_found", extra={"order_id": order_id})
            return {"status": "not_found", "variants": []}

        items = full_order["Items"]["OrderItems"]
        marketplace_id = full_order["Order"]["MarketplaceId"]

        products: list[dict[str, Any]] = []
        for item in items:
            asin = item.get("ASIN")
            if not asin:
                continue
            product = await self.get_catalog_item_attributes(asin, marketplace_id)
            products.append({
                "asin": asin,
                "marketplace_id": marketplace_id,
                "order_item": item,
                "product": product,
            })
        self.logger.debug(
            "get_products_by_order_id",
            extra={"order_id": order_id, "products": products},
        )
        return {
            "order": full_order["Order"],
            "products": products,
        }
