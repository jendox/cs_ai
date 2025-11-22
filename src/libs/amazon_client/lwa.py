from __future__ import annotations

import typing
from dataclasses import dataclass

import httpx
from httpx import Request, Response

if typing.TYPE_CHECKING:
    from .client import AsyncAmazonClient

ACCESS_TOKEN_EXPIRY_SECONDS = 3600
ACCESS_TOKEN_EXPIRY_SHIFT = 30


@dataclass
class LWAToken:
    value: str | None = None
    expires_at: float = 0.0


class LWAAuthentication(httpx.Auth):
    requires_request_body = False

    def __init__(self, client: AsyncAmazonClient) -> None:
        self._client = client

    async def async_auth_flow(
        self, request: Request,
    ) -> typing.AsyncGenerator[Request, Response]:
        access_token = await self._client._lwa_access_token()
        request.headers["x-amz-access-token"] = access_token
        yield request
