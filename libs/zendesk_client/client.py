import logging
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Literal, Self

import httpx
import pydantic
from pydantic import BaseModel

from .models import Brand, Comment, Ticket, TicketStatus

MAX_HTTPX_CONNECTIONS = 20
DEFAULT_HTTPX_TIMEOUT = 10.0

CommentKind = Literal["comment_public", "comment_private"]


class ZendeskSettings(BaseModel):
    email: str
    token: str
    subdomain: str
    review_mode: bool

    @classmethod
    def load(cls) -> Self:
        return cls(
            email=os.getenv("ZENDESK_EMAIL"),
            token=os.getenv("ZENDESK_TOKEN"),
            subdomain=os.getenv("ZENDESK_SUBDOMAIN"),
            review_mode=os.getenv("ZENDESK_REVIEW_MODE", default="true").lower() == "true",
        )


class ZendeskClient:
    def __init__(self, http_client: httpx.AsyncClient, settings: ZendeskSettings) -> None:
        http_client.auth = (f"{settings.email}/token", settings.token)
        http_client.base_url = f"https://{settings.subdomain}.zendesk.com/api/v2"
        self.http_client = http_client
        self.review_mode = settings.review_mode
        self.logger = logging.getLogger("zendesk_client")

    async def get_ticket_by_id(self, ticket_id: int) -> Ticket:
        url = f"/tickets/{ticket_id}.json"
        response = await self.http_client.get(url)
        ticket = response.json().get("ticket")
        return Ticket.model_validate(ticket)

    async def get_ticket_comments(self, ticket_id: int) -> list[Comment]:
        url = f"/tickets/{ticket_id}/comments.json"
        response = await self.http_client.get(url)
        data = response.json()
        return [Comment.model_validate(c) for c in data.get("comments")]

    async def add_comment(self, ticket_id: int, comment_text: str) -> None:
        public = False if self.review_mode else True
        body = {"ticket": {"comment": {"body": comment_text, "public": public}}}
        url = f"/tickets/{ticket_id}.json"
        response = await self.http_client.put(url, json=body)
        response.raise_for_status()

    @staticmethod
    def _make_url(cursor: str, start_time: int) -> str:
        if cursor:
            return f"/incremental/tickets/cursor.json?cursor={cursor}"
        return f"/incremental/tickets/cursor.json?start_time={start_time}"

    async def _get_incremental_tickets(
        self,
        start_time: int,
        cursor: str | None = None,
    ) -> tuple[list[dict], str, bool]:
        url = self._make_url(cursor, start_time)
        response = await self.http_client.get(url)
        response.raise_for_status()
        cursor_data = response.json()
        tickets = cursor_data["tickets"]
        self.logger.info(
            "Start time: %d - Tickets count: %d", start_time, len(tickets),
        )
        after_cursor = cursor_data["after_cursor"]
        end_of_stream = cursor_data["end_of_stream"]
        return tickets, after_cursor, end_of_stream

    async def _iter_incremental_tickets(
        self,
        start_time: int,
    ) -> AsyncGenerator[list[dict], None]:
        cursor: str | None = None
        while True:
            try:
                tickets, after_cursor, end_of_stream = await self._get_incremental_tickets(start_time, cursor)
                if not tickets:
                    if end_of_stream:
                        break
                    cursor = after_cursor
                    continue
                yield tickets
                if end_of_stream:
                    break
                cursor = after_cursor
            except httpx.HTTPError as error:
                self.logger.warning(error)
                break

    async def iter_tickets(
        self,
        updated_after: datetime,
        brand: Brand | None = None,
        statuses: set[TicketStatus] | None = None,
    ) -> AsyncGenerator[Ticket, None]:
        start_time = int(updated_after.timestamp())
        async for batch_tickets in self._iter_incremental_tickets(start_time):
            for raw_ticket in batch_tickets:
                try:
                    ticket = Ticket.model_validate(raw_ticket)
                    if brand is not None and ticket.brand != brand:
                        continue
                    if statuses is not None and ticket.status not in statuses:
                        continue
                    # TODO: здесь нужно удостовериться, что Zendesk новому тикету тоже сразу добавляет поле updated_at
                    if not ticket.updated_at or ticket.updated_at < updated_after:
                        continue
                    yield ticket
                except pydantic.ValidationError as error:
                    self.logger.warning("Ticket validation error: %s", str(error))


@asynccontextmanager
async def create_zendesk_client(settings: ZendeskSettings) -> AsyncGenerator[ZendeskClient, None]:
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(DEFAULT_HTTPX_TIMEOUT, connect=DEFAULT_HTTPX_TIMEOUT),
        limits=httpx.Limits(
            max_connections=MAX_HTTPX_CONNECTIONS,
            max_keepalive_connections=MAX_HTTPX_CONNECTIONS // 2,
        ),
    ) as http_client:
        http_client.headers.update(
            {
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        yield ZendeskClient(http_client, settings)
