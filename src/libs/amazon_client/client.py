import csv
import gzip
import http
import io
import json
import logging
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any, ClassVar, Self

import anyio
import httpx
from httpx import AsyncClient, Limits, Timeout

from src.config import AmazonSettings

from . import helpers
from .enums import (
    AUTH_URL,
    HTTPX_DEFAULT_TIMEOUT,
    HTTPX_READ_TIMEOUT,
    SPAPI_ENDPOINTS,
    EndpointRegion,
    MarketplaceId,
    ReportStatus,
    ReportType,
)
from .exceptions import (
    AmazonAsyncClientError,
    AmazonAuthError,
    AmazonNetworkError,
)
from .helpers import build_normalized_product
from .lwa import ACCESS_TOKEN_EXPIRY_SECONDS, ACCESS_TOKEN_EXPIRY_SHIFT, LWAAuthentication, LWAToken
from .schemes import FullOrder, MerchantListingRow, NormalizedProduct, OrderItemsSummary, OrderSummary
from .schemes.product_attributes import CatalogItemAttributes


class AsyncAmazonClient(httpx.AsyncClient):
    _initialized_instance: ClassVar[Self | None] = None

    def __init__(self, settings: AmazonSettings, *args, **kwargs) -> None:
        self._settings = settings
        self.logger = logging.getLogger("async_amazon_client")
        self._lwa_token: LWAToken = LWAToken()
        self._lock: anyio.Lock = anyio.Lock()

        self._rate_limits: dict[str, float] = {
            "get_order": 60.0,
            "get_order_items": 2.0,
            "get_catalog_item_attributes": 2.0,
            # "create_report": 60.0,
            # "wait_done": 15.0,
            # "download_document_meta": 2.0,
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

    # ---- Request with Error Handling ----

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

    # ---- Catalog Item Attributes ----

    async def get_catalog_item_attributes(self, asin: str, marketplace_id: MarketplaceId) -> NormalizedProduct:
        url = f"/catalog/2022-04-01/items/{asin}"
        params = {
            "marketplaceIds": [marketplace_id.value],
            "includedData": "attributes",
        }
        response = await self._sp_request("get_catalog_item_attributes", "GET", url=url, params=params)
        data = response.json()
        return build_normalized_product(
            catalog_item=CatalogItemAttributes.model_validate(data),
            marketplace_id=marketplace_id.value,
        )

    # ---- Private Helpers for Report Loading ----

    async def _create_report(self, payload: dict[str, Any]) -> str:
        url = "/reports/2021-06-30/reports"
        response = await self._sp_request("create_report", "POST", url=url, json=payload)
        return response.json()["reportId"]

    async def _wait_done(
        self,
        report_id: str,
        poll: float = 15,
        timeout: float = 1800,
    ) -> str:
        end = time.time() + timeout
        last = None
        url = f"/reports/2021-06-30/reports/{report_id}"
        while time.time() < end:
            response = await self._sp_request("wait_done", "GET", url=url)
            status_data = response.json()
            report_status = ReportStatus(status_data.get("processingStatus", "FATAL"))
            if report_status == ReportStatus.DONE:
                return status_data["reportDocumentId"]
            if report_status in ReportStatus.failed():
                raise RuntimeError(f"Report failed: {report_status.value} :: {status_data}")
            last = report_status
            await anyio.sleep(poll)
        raise TimeoutError(f"Not ready; last={last.value}")

    async def _download_document(self, document_id: str) -> dict[str, Any] | str:
        url = f"/reports/2021-06-30/documents/{document_id}"
        response = await self._sp_request("download_document", "GET", url=url)
        meta = response.json()
        url = meta["url"]
        self.logger.info("get_merchant_listings_all_data.download_document", extra={"url": url})
        async with AsyncClient(timeout=self.timeout) as client:
            response = await client.get(url=url)
            response.raise_for_status()
            data = response.content
            encoding = response.charset_encoding or "utf-8"
        if meta.get("compressionAlgorithm") == "GZIP":
            data = gzip.GzipFile(fileobj=io.BytesIO(data)).read()
        try:
            return json.loads(data.decode())  # JSON
        except Exception:
            return data.decode(encoding=encoding)  # CSV/TSV

    # =============== Merchant Listing All Data ===============

    async def get_merchant_listings_all_data(self, marketplace_id: MarketplaceId) -> list[MerchantListingRow]:
        extra = {"marketplace_id": marketplace_id.value}
        payload = {
            "reportType": ReportType.GET_MERCHANT_LISTINGS_ALL_DATA.value,
            "marketplaceIds": [marketplace_id.value],
        }
        try:
            report_id = await self._create_report(payload)
            self.logger.info("get_merchant_listings_all_data", extra={**extra, "report_id": report_id})
            document_id = await self._wait_done(report_id)
            self.logger.info("get_merchant_listings_all_data", extra={**extra, "document_id": document_id})
            data = await self._download_document(document_id)
            self.logger.info("get_merchant_listings_all_data", extra={**extra, "text": data[:200]})
            file = io.StringIO(data)
            reader = csv.DictReader(file, delimiter="\t")
            merchant_listing: list[MerchantListingRow] = []

            for row in reader:
                if row.get("status").strip().lower() == "incomplete":
                    continue

                asin = (row.get("asin1") or "").strip()
                sku = (row.get("seller-sku") or "").strip()
                if not asin or not sku:
                    continue

                model = MerchantListingRow.model_validate({
                    "asin": asin,
                    "seller_sku": sku,
                    "item_name": (row.get("item-name") or "").strip(),
                    "item_description": (row.get("item-description") or "").strip() or None,
                    "fulfillment_channel": (row.get("fulfillment-channel") or "").strip() or None,
                })
                merchant_listing.append(model)

            return merchant_listing

        except Exception as exc:
            self.logger.error("get_merchant_listings_all_data.failed", extra={**extra, "error": str(exc)})
            return []
