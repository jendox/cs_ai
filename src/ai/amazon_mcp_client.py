from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any, ClassVar, Self

import anyio
import fastmcp


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
        self.logger.info("amazon_mcp.get_order", extra={"order_id": order_id})
        client = await self._get_client()
        result = await client.call_tool("get_order", {"order_id": order_id})
        return json.loads(result.content[0].text)

    async def get_order_items(self, order_id: str) -> dict[str, Any]:
        self.logger.info("amazon_mcp.get_order_items", extra={"order_id": order_id})
        client = await self._get_client()
        result = await client.call_tool("get_order_items", {"order_id": order_id})
        return json.loads(result.content[0].text)

    async def get_full_order(self, order_id: str) -> dict[str, Any]:
        self.logger.info("amazon_mcp.get_full_order", extra={"order_id": order_id})
        client = await self._get_client()
        result = await client.call_tool("get_full_order", {"order_id": order_id})
        return json.loads(result.content[0].text)

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
