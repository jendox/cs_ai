"""One-off backfill for tickets incorrectly marked as service by `platform_combo`.

Background: the `_rule_platform_combo.has_no_sender` branch produced
false-positive SERVICE decisions for real customer messages routed through
marketplace integrations (Amazon/eBay/TikTok) that strip the sender email.
The rule was removed; this script re-classifies the affected tickets with
the fixed pipeline and, when appropriate, re-queues them for an initial reply.

Usage (from project root):

    uv run python scripts/reclassify_platform_combo_tickets.py            # dry-run
    uv run python scripts/reclassify_platform_combo_tickets.py --apply    # actually write
    uv run python scripts/reclassify_platform_combo_tickets.py --apply --requeue
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import text  # noqa: E402
from src.ai.client_pool import LLMClientPool  # noqa: E402

from src.ai.config.prompt import LLMPromptStorage  # noqa: E402
from src.ai.config.runtime import LLMRuntimeSettingsStorage  # noqa: E402
from src.ai.context import LLMContext  # noqa: E402
from src.config import get_app_settings  # noqa: E402
from src.db import session_local  # noqa: E402
from src.db.repositories import (  # noqa: E402
    CLASSIFICATION_DECISION_CUSTOMER,
    TicketsRepository,
)
from src.db.sa import Database  # noqa: E402
from src.jobs.models import InitialReplyMessage, JobType  # noqa: E402
from src.jobs.rabbitmq_queue import create_job_queue  # noqa: E402
from src.libs.zendesk_client.client import ZendeskTicketNotFound, create_zendesk_client  # noqa: E402
from src.services.ticket_classification import TicketClassificationService  # noqa: E402

logger = logging.getLogger("reclassify_platform_combo")


AFFECTED_RULE = "platform_combo"


async def _find_affected_ticket_ids() -> list[int]:
    """Return tickets whose *latest* audit row is a platform_combo SERVICE."""
    query = text(
        """
        WITH latest AS (
            SELECT DISTINCT ON (ticket_id)
                   ticket_id, decision, source, rule, detail
              FROM ticket_classification_audits
             ORDER BY ticket_id, created_at DESC, id DESC
        )
        SELECT ticket_id
          FROM latest
         WHERE rule = :rule
           AND decision = 'service'
         ORDER BY ticket_id
        """,
    )
    async with session_local() as session:
        result = await session.execute(query, {"rule": AFFECTED_RULE})
        return [row[0] for row in result]


async def _reclassify_one(
    ticket_id: int,
    *,
    zendesk_client,
    classification_service: TicketClassificationService,
    apply: bool,
    requeue: bool,
    amqp_url: str,
) -> None:
    settings = get_app_settings()
    try:
        zendesk_ticket = await zendesk_client.get_ticket(ticket_id)
    except ZendeskTicketNotFound:
        logger.warning("ticket.not_found_in_zendesk", extra={"ticket_id": ticket_id})
        return
    except Exception as exc:
        logger.error("ticket.fetch_failed", extra={"ticket_id": ticket_id, "error": str(exc)})
        return

    brand_id = zendesk_ticket.brand_id
    brand = settings.brand.brand_for_id(brand_id) if brand_id else None
    if brand is None:
        logger.warning(
            "ticket.unsupported_brand",
            extra={"ticket_id": ticket_id, "brand_id": brand_id},
        )
        return

    zendesk_ticket.id = zendesk_ticket.id or ticket_id

    if not apply:
        logger.info(
            "dry_run.would_reclassify",
            extra={"ticket_id": ticket_id, "brand": brand.value, "brand_id": brand_id},
        )
        return

    async with session_local() as session:
        async with session.begin():
            result = await classification_service.classify_and_store(
                session,
                ticket=zendesk_ticket,
                brand=brand,
                force=True,
            )

    logger.info(
        "reclassified",
        extra={
            "ticket_id": ticket_id,
            "decision": result.decision,
            "source": result.source,
            "rule": result.rule,
            "detail": result.detail,
        },
    )

    if result.decision != CLASSIFICATION_DECISION_CUSTOMER:
        return

    # Restore observability so the poller will continue tracking the ticket.
    async with session_local() as session:
        async with session.begin():
            await TicketsRepository(session).set_observing(ticket_id, observing=True)

    if not requeue:
        return

    message = InitialReplyMessage(ticket=zendesk_ticket)
    job_queue = await create_job_queue(amqp_url, brand_id)
    try:
        await job_queue.publish(
            JobType.INITIAL_REPLY,
            message.model_dump(mode="json", by_alias=True, exclude_none=True),
            brand_id=brand_id,
        )
    finally:
        await job_queue.close()

    logger.info("requeued.initial_reply", extra={"ticket_id": ticket_id, "brand_id": brand_id})


async def _run(apply: bool, requeue: bool) -> None:
    settings = get_app_settings()
    async with Database.lifespan(url=settings.postgres.url):
        ticket_ids = await _find_affected_ticket_ids()
        if not ticket_ids:
            logger.info("no_affected_tickets")
            return

        logger.info(
            "found_affected_tickets",
            extra={"count": len(ticket_ids), "ticket_ids": ticket_ids},
        )

        llm_context = LLMContext(
            client_pool=LLMClientPool(settings.llm),
            runtime_storage=LLMRuntimeSettingsStorage(),
            prompt_storage=LLMPromptStorage(),
            amazon_mcp_client=None,
        )
        classification_service = TicketClassificationService(llm_context)

        async with create_zendesk_client(settings.zendesk) as zendesk_client:
            for ticket_id in ticket_ids:
                await _reclassify_one(
                    ticket_id,
                    zendesk_client=zendesk_client,
                    classification_service=classification_service,
                    apply=apply,
                    requeue=requeue,
                    amqp_url=settings.rabbitmq.amqp_url,
                )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually run reclassification and write audit rows. Default is dry-run.",
    )
    parser.add_argument(
        "--requeue",
        action="store_true",
        help="If a ticket ends up as 'customer', re-publish an initial_reply job. "
        "Requires --apply.",
    )
    args = parser.parse_args()

    if args.requeue and not args.apply:
        parser.error("--requeue requires --apply")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s %(extra)s",
    )
    # The stdlib formatter doesn't natively render "extra". Fall back to a minimal one:
    for handler in logging.getLogger().handlers:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"),
        )

    asyncio.run(_run(apply=args.apply, requeue=args.requeue))


if __name__ == "__main__":
    main()
