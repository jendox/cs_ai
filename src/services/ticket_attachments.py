from sqlalchemy.ext.asyncio import AsyncSession

from src.db.repositories import (
    CLASSIFICATION_DECISION_CUSTOMER,
    TicketClassificationAuditsRepository,
    TicketCommentAttachmentsRepository,
    TicketNotFound,
    TicketsRepository,
)
from src.libs.zendesk_client.models import Comment


async def should_store_ticket_attachments(session: AsyncSession, ticket_id: int) -> bool:
    tickets_repo = TicketsRepository(session)
    try:
        ticket = await tickets_repo.get_ticket_by_id(ticket_id)
    except TicketNotFound:
        return False

    if not ticket.observing:
        return False

    latest_audit = await TicketClassificationAuditsRepository(session).get_latest_by_ticket(ticket_id)
    return latest_audit is None or latest_audit.decision == CLASSIFICATION_DECISION_CUSTOMER


async def store_ticket_comment_attachments(
    session: AsyncSession,
    *,
    ticket_id: int,
    comments: list[Comment],
) -> int:
    if not await should_store_ticket_attachments(session, ticket_id):
        return 0

    attachments_repo = TicketCommentAttachmentsRepository(session)
    return await attachments_repo.upsert_from_comments(ticket_id, comments)
