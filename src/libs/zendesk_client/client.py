import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import datetime

import httpx
import pydantic
from httpx import Response

from src.config import ZendeskSettings

from .models import Brand, Comment, Ticket, TicketStatus

MAX_HTTPX_CONNECTIONS = 20
DEFAULT_HTTPX_TIMEOUT = 10.0


class ZendeskClientError(Exception):
    """Base exception type for ZendeskClient-related errors."""


class ZendeskTicketNotFound(ZendeskClientError):
    """Raised when a requested ticket cannot be found in the Zendesk API response."""


class ZendeskCommentNotFound(ZendeskClientError):
    """Raised when a requested comment cannot be found in the Zendesk API response."""


class ZendeskClient:
    """Asynchronous client for interacting with the Zendesk Tickets API.

    This client is a thin wrapper around ``httpx.AsyncClient`` that:
    - handles authentication and base URL configuration,
    - provides typed methods for common Zendesk ticket operations,
    - centralizes HTTP error logging and re-raising.

    Parameters
    ----------
    http_client : httpx.AsyncClient
        Preconfigured HTTP client instance. Its ``auth`` field will be
        overwritten based on the provided settings.
    settings : ZendeskSettings
        Zendesk configuration including subdomain, email, API token and review mode.
    """

    def __init__(self, http_client: httpx.AsyncClient, settings: ZendeskSettings) -> None:
        http_client.auth = (f"{settings.email}/token", settings.token.get_secret_value())
        self._http_client = http_client
        self.review_mode = settings.review_mode
        self.logger = logging.getLogger("zendesk_client")

    async def get_ticket(self, ticket_id: int) -> Ticket:
        """Retrieve a single ticket by its Zendesk ID.

        Args:
            ticket_id (int): Unique identifier of the ticket.

        Returns:
            Ticket: Parsed ticket model returned by the Zendesk API.

        Raises:
            httpx.HTTPError: If the HTTP request fails (network error or 4xx/5xx status).
            TicketNotFound: If the API response does not contain a 'ticket' object.
        """
        response = await self._request(
            api="get_ticket",
            method="GET",
            url=f"/tickets/{ticket_id}.json",
        )
        ticket = response.json().get("ticket")
        if ticket is not None:
            return Ticket.model_validate(ticket)
        raise ZendeskTicketNotFound(f"Ticket {ticket_id} not found.")

    async def get_ticket_comments(self, ticket_id: int) -> list[Comment]:
        """Retrieve all comments associated with a ticket.

        Args:
            ticket_id (int): Unique identifier of the ticket.

        Returns:
            list[Comment]: A list of parsed comment models. The list may be empty.

        Raises:
            httpx.HTTPError: If the HTTP request fails or returns a non-success status code.
        """
        response = await self._request(
            api="get_ticket_comments",
            method="GET",
            url=f"/tickets/{ticket_id}/comments.json",
        )
        comments = response.json().get("comments", [])
        return [Comment.model_validate(comment) for comment in comments]

    async def get_ticket_comment(self, ticket_id: int, comment_id: int) -> Comment:
        """Retrieve a specific comment from a ticket by its comment ID.

        Note:
            Zendesk does not expose a dedicated endpoint for individual comments.
            This method fetches all ticket comments and searches for the target ID.

        Args:
            ticket_id (int): Identifier of the ticket containing the comment.
            comment_id (int): Identifier of the desired comment.

        Returns:
            Comment: Parsed comment model for the specified comment ID.

        Raises:
            httpx.HTTPError: If the HTTP request fails.
            CommentNotFound: If the comment with the specified ID is not found.
        """
        response = await self._request(
            api="get_ticket_comment",
            method="GET",
            url=f"/tickets/{ticket_id}/comments.json",
        )
        comments = response.json().get("comments", [])
        for comment in comments:
            if comment.get("id") == comment_id:
                return Comment.model_validate(comment)
        self.logger.warning(
            "get_ticket_comment.not_found",
            extra={"ticket_id": ticket_id, "comment_id": comment_id},
        )
        raise ZendeskCommentNotFound(f"Comment {comment_id} not found in ticket {ticket_id}.")

    async def add_comment(
        self,
        ticket_id: int,
        comment_text: str,
        *,
        public: bool,
    ) -> None:
        """Add a new comment to a ticket.

        The comment will be public or private depending on the client's review mode.

        Args:
            ticket_id (int): Identifier of the ticket to which the comment is added.
            comment_text (str): Text content of the new comment.
            public (bool): Public comment if True, else Internal note

        Returns:
            None

        Raises:
            httpx.HTTPError: If the HTTP request fails or Zendesk returns an error status.
        """
        payload = {"ticket": {"comment": {"body": comment_text, "public": public}}}

        await self._request(
            api="add_comment",
            method="PUT",
            url=f"/tickets/{ticket_id}.json",
            json=payload,
        )

    async def iter_updated_tickets(
        self,
        updated_after: datetime,
        brand: Brand | None = None,
        statuses: set[TicketStatus] | None = None,
    ) -> AsyncGenerator[Ticket, None]:
        """Iterate over tickets updated after a given timestamp.

        This method uses the Zendesk incremental tickets API and applies additional
        filtering based on brand and ticket status.

        Args:
            updated_after (datetime): Only tickets updated strictly after this timestamp are yielded.
            brand (Brand | None, optional): If provided, only tickets belonging to this brand are included.
            statuses (set[TicketStatus] | None, optional): Allowed ticket statuses. If provided, only
                tickets whose status is in the set are yielded.

        Yields:
            Ticket: Tickets matching the filter conditions.

        Raises:
            httpx.HTTPError: If a request to the incremental tickets API fails.
        """
        self.logger.debug("tickets.iter_start", extra={"start_time": str(updated_after)})
        start_time = int(updated_after.timestamp())
        async for batch_tickets in self._iter_incremental_tickets(start_time):
            for raw_ticket in batch_tickets:
                try:
                    ticket = Ticket.model_validate(raw_ticket)
                    if brand is not None and ticket.brand != brand:
                        continue
                    if statuses is not None and ticket.status not in statuses:
                        continue
                    if not ticket.updated_at or ticket.updated_at < updated_after:
                        continue
                    yield ticket
                except pydantic.ValidationError:
                    self.logger.warning("ticket.validation_error")

    async def _request(self, *, api: str, method: str, url: str, **kwargs) -> Response:
        """Perform a low-level HTTP request with unified logging and error handling.

        Args:
            api (str): Logical operation name used for structured logging.
            method (str): HTTP method (e.g., "GET", "POST", "PUT").
            url (str): Zendesk API endpoint path (relative to base URL).
            **kwargs: Additional parameters passed directly to ``httpx.AsyncClient.request``.

        Returns:
            httpx.Response: Successful HTTP response object.

        Raises:
            httpx.HTTPStatusError: If Zendesk returns a non-2xx status.
            httpx.RequestError: If a network-level error occurs.
        """
        try:
            response = await self._http_client.request(method, url, **kwargs)
            response.raise_for_status()
            return response
        except httpx.HTTPStatusError as error:
            status = error.response.status_code
            self.logger.warning(
                f"{api}.http_error",
                extra={
                    "status": status,
                    "url": str(error.request.url),
                    "method": str(error.request.method),
                    "error": error.response.text[:500],
                },
            )
            raise
        except httpx.RequestError as error:
            self.logger.warning(
                f"{api}.request_error",
                extra={
                    "url": str(error.request.url) if error.request else url,
                    "error": str(error),
                },
            )
            raise

            # ========== Incremental Tickets ==========

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
        """Retrieve a batch of incremental tickets from Zendesk.

        Args:
            start_time (int): Unix timestamp used as the initial incremental export point.
            cursor (str | None, optional): Pagination cursor returned by a previous batch.

        Returns:
            tuple[list[dict], str, bool]:
                - Raw ticket dictionaries returned by Zendesk.
                - Cursor string to be used for the next request.
                - Boolean flag indicating end of stream.

        Raises:
            httpx.HTTPError: If the underlying HTTP request fails.
        """
        url = self._make_url(cursor, start_time)
        response = await self._request(
            api="_get_incremental_tickets",
            method="GET",
            url=url,
        )
        cursor_data = response.json()
        tickets = cursor_data["tickets"]
        self.logger.debug("tickets.batch", extra={"count": len(tickets), "start_time": start_time})
        after_cursor = cursor_data["after_cursor"]
        end_of_stream = cursor_data["end_of_stream"]
        return tickets, after_cursor, end_of_stream

    async def _iter_incremental_tickets(
        self,
        start_time: int,
    ) -> AsyncGenerator[list[dict], None]:
        """Internal generator yielding batches of raw tickets from the incremental API.

        Args:
            start_time (int): Unix timestamp defining the incremental export starting point.

        Yields:
            list[dict]: Each yielded value is a batch of raw ticket dictionaries.

        Raises:
            httpx.HTTPError: If a network or API error occurs during iteration.
        """
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


@asynccontextmanager
async def create_zendesk_client(settings: ZendeskSettings) -> AsyncGenerator[ZendeskClient, None]:
    """Create and yield a configured ZendeskClient instance.

    This async context manager:
      - configures a httpx.AsyncClient with authentication and base URL,
      - constructs a ZendeskClient bound to that client,
      - ensures proper cleanup of network resources.

    Args:
        settings (ZendeskSettings): Zendesk configuration including subdomain,
            agent email, API token, and review mode.

    Yields:
        ZendeskClient: Ready-to-use client instance wrapped in an async context.

    Raises:
        httpx.HTTPError: If initial connectivity or authentication fails.
    """
    async with httpx.AsyncClient(
        base_url=f"https://{settings.subdomain}.zendesk.com/api/v2",
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
