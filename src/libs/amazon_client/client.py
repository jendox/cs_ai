import http
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import ClassVar, Self

import anyio
import httpx
from httpx import Limits, Timeout

from src.config import AmazonSettings

from . import helpers
from .enums import AUTH_URL, HTTPX_DEFAULT_TIMEOUT, HTTPX_READ_TIMEOUT, SPAPI_ENDPOINTS, EndpointRegion
from .exceptions import (
    AmazonAsyncClientError,
    AmazonAuthError,
    AmazonNetworkError,
)
from .lwa import ACCESS_TOKEN_EXPIRY_SECONDS, ACCESS_TOKEN_EXPIRY_SHIFT, LWAAuthentication, LWAToken
from .schemes import FullOrder, OrderItemsSummary, OrderSummary


class AsyncAmazonClient(httpx.AsyncClient):
    _initialized_instance: ClassVar[Self | None] = None

    def __init__(self, settings: AmazonSettings, *args, **kwargs) -> None:
        self._settings = settings
        self._lwa_token: LWAToken = LWAToken()
        self._lock: anyio.Lock = anyio.Lock()

        self._rate_limits: dict[str, float] = {
            "get_order": 60.0,
            "get_order_items": 2.0,
        }
        self._last_call: dict[str, float] = {}
        self._rate_lock: anyio.Lock = anyio.Lock()

        # ---- Order Items Cache
        self._order_items_cache_ttl: float = 300.0
        self._order_items_cache: dict[str, tuple[float, OrderItemsSummary]] = {}
        self._order_items_cache_lock: anyio.Lock = anyio.Lock()

        kwargs.setdefault("auth", LWAAuthentication(self))

        super().__init__(*args, **kwargs)
        self.headers["accept"] = "application/json"
        self.headers["user-agent"] = "ai-cs/0.1.0 (Language=Python3)"

    @classmethod
    @asynccontextmanager
    async def setup(
        cls,
        settings: AmazonSettings,
        max_connection: int,
        region: EndpointRegion,
    ) -> AsyncGenerator[None]:
        base_url = SPAPI_ENDPOINTS[region]
        cls._initialized_instance = cls(
            base_url=base_url,
            settings=settings,
            timeout=Timeout(
                timeout=HTTPX_DEFAULT_TIMEOUT,
                read=HTTPX_READ_TIMEOUT,
            ),
            limits=Limits(
                max_connections=max_connection,
                max_keepalive_connections=max_connection,
            ),
        )
        async with cls._initialized_instance:
            yield

    @classmethod
    def get_initialized_instance(cls) -> Self:
        if initialized_instance := cls._initialized_instance:
            return initialized_instance
        raise AmazonAsyncClientError(
            "AsyncAmazonClient is not initialized. Use setup() method to initialize client.",
        )

    # ---- LWA токен ----

    async def _lwa_access_token(self) -> str:
        async with self._lock:
            now = time.time()

            if self._lwa_token.value and now < self._lwa_token.expires_at - ACCESS_TOKEN_EXPIRY_SHIFT:
                return self._lwa_token.value

            data = {
                "grant_type": "refresh_token",
                "refresh_token": self._settings.lwa_refresh_token.get_secret_value(),
                "client_id": self._settings.lwa_client_id.get_secret_value(),
                "client_secret": self._settings.lwa_client_secret.get_secret_value(),
            }
            headers = {"Content-Type": "application/x-www-form-urlencoded"}

            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    response = await client.post(url=AUTH_URL, data=data, headers=headers)
            except httpx.RequestError as exc:
                raise AmazonNetworkError(
                    f"Network error while requesting LWA token: {exc}",
                ) from exc
            if response.status_code >= http.HTTPStatus.BAD_REQUEST:
                body = response.text[:500]
                raise AmazonAuthError(
                    message=f"LWA auth failed ({response.status_code}): {body}",
                    status_code=response.status_code,
                    body=response.text,
                )
            try:
                token_data = response.json()
            except ValueError:
                raise AmazonAuthError(
                    message="LWA returned invalid JSON while fetching token.",
                    status_code=response.status_code,
                    body=response.text,
                )

            if "access_token" not in token_data:
                raise AmazonAuthError(
                    message="LWA response missing 'access_token'.",
                    status_code=response.status_code,
                    body=response.text,
                )
            access_token = token_data["access_token"]

            expires_in = float(token_data.get("expires_in", ACCESS_TOKEN_EXPIRY_SECONDS))
            self._lwa_token.value = access_token
            self._lwa_token.expires_at = now + expires_in

            return access_token

    # ---- Throttling ----

    async def _throttle(self, key: str) -> None:
        min_interval = self._rate_limits.get(key)
        if not min_interval:
            return
        async with self._rate_lock:
            now = time.time()
            last = self._last_call.get(key, 0.0)
            elapsed = now - last
            wait_for = min_interval - elapsed
            if wait_for > 0:
                await anyio.sleep(wait_for)
                now = time.time()
            self._last_call[key] = now

    # ---- Request with Error Handling

    async def _request_with_errors(self, method: str, url: str, **kwargs) -> httpx.Response:
        try:
            response = await super().request(method, url, **kwargs)
        except httpx.RequestError as exc:
            raise AmazonNetworkError(f"Network error while calling {url}: {exc}") from exc

        if response.is_success:
            return response

        return helpers.process_errors(response)

    # ---- SP-API Request Helper ----

    async def _sp_request(
        self,
        key: str,
        method: str,
        url: str,
        **kwargs,
    ) -> httpx.Response:
        await self._throttle(key)
        return await self._request_with_errors(method, url, **kwargs)

    # ---- Public Methods ----

    async def get_full_order(self, order_id: str) -> FullOrder:
        order = await self.get_order(order_id)
        items = await self.get_order_items(order_id)
        return FullOrder(order=order, items=items)

    async def get_order(self, order_id: str) -> OrderSummary:
        url = f"/orders/v0/orders/{order_id}"
        response = await self._sp_request("get_order", "GET", url)
        data = response.json()
        return OrderSummary.model_validate(data.get("payload"))

    async def get_order_items(self, order_id: str) -> OrderItemsSummary:
        cached = await self._get_cached_order_items(order_id)
        if cached is not None:
            return cached

        url = f"/orders/v0/orders/{order_id}/orderItems"
        response = await self._sp_request("get_order_items", "GET", url)
        data = response.json()
        items = OrderItemsSummary.model_validate(data.get("payload"))
        await self._set_cached_order_items(order_id, items)
        return items

    async def _get_cached_order_items(self, order_id: str) -> OrderItemsSummary | None:
        async with self._order_items_cache_lock:
            cached = self._order_items_cache.get(order_id)
            if not cached:
                return None
            ts, items = cached
            if time.time() - ts > self._order_items_cache_ttl:
                self._order_items_cache.pop(order_id, None)
                return None
            return items

    async def _set_cached_order_items(self, order_id: str, items: OrderItemsSummary) -> None:
        async with self._order_items_cache_lock:
            self._order_items_cache[order_id] = (time.time(), items)
