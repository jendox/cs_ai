from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass

import httpx

from src.db.models import PostChannel
from src.db.repositories import OurPostsRepository, ReplyAttemptCreate, TicketReplyAttemptsRepository
from src.libs.zendesk_client.client import ZendeskClient


@dataclass(frozen=True)
class PostCommentResult:
    success: bool
    error: str | None = None
    zendesk_comment_id: int | None = None


@dataclass(frozen=True)
class ReplyPostingContext:
    ticket_id: int
    brand_id: int
    job_type: str
    channel: PostChannel
    prompt_key: str
    iteration_id: str | None = None


class ReplyPostingService:
    def __init__(
        self,
        *,
        zendesk_client: ZendeskClient,
        logger: logging.Logger,
        our_posts_repo: OurPostsRepository,
        reply_attempts_repo: TicketReplyAttemptsRepository,
    ) -> None:
        self._zendesk_client = zendesk_client
        self._logger = logger
        self._our_posts_repo = our_posts_repo
        self._reply_attempts_repo = reply_attempts_repo

    async def post_reply(self, context: ReplyPostingContext, reply: str) -> bool:
        if not reply:
            empty_attempt = await self._reply_attempts_repo.create_empty_reply(
                ReplyAttemptCreate(
                    ticket_id=context.ticket_id,
                    brand_id=context.brand_id,
                    job_type=context.job_type,
                    channel=context.channel,
                    prompt_key=context.prompt_key,
                    iteration_id=context.iteration_id,
                ),
            )
            self._logger.info("reply_attempt.empty", extra={"attempt_id": empty_attempt.id})
            return False

        body_hash = self._reply_body_hash(reply)
        attempt = await self._reply_attempts_repo.create_generated(
            ReplyAttemptCreate(
                ticket_id=context.ticket_id,
                brand_id=context.brand_id,
                job_type=context.job_type,
                channel=context.channel,
                body_hash=body_hash,
                body=reply,
                prompt_key=context.prompt_key,
                iteration_id=context.iteration_id,
            ),
        )
        self._logger.info("reply_attempt.generated", extra={"attempt_id": attempt.id})

        saved = await self._save_reply(context, reply, body_hash)
        if not saved:
            await self._reply_attempts_repo.mark_skipped_duplicate(attempt.id)
            self._logger.info("reply_attempt.duplicate", extra={"attempt_id": attempt.id})
            return True

        post_result = await self._post_comment(
            context.ticket_id,
            reply,
            public=context.channel == PostChannel.PUBLIC,
        )
        if post_result.success:
            await self._reply_attempts_repo.mark_posted(
                attempt.id,
                zendesk_comment_id=post_result.zendesk_comment_id,
            )
            self._logger.info(
                "reply_attempt.posted",
                extra={
                    "attempt_id": attempt.id,
                    "zendesk_comment_id": post_result.zendesk_comment_id,
                },
            )
            return True

        error = post_result.error or "Zendesk comment post failed"
        await self._our_posts_repo.delete_our_post(ticket_id=context.ticket_id, body_hash=body_hash)
        await self._reply_attempts_repo.mark_failed(attempt.id, error=error)
        self._logger.warning("reply_attempt.failed", extra={"attempt_id": attempt.id, "error": error})
        return False

    async def _save_reply(
        self,
        context: ReplyPostingContext,
        body: str,
        body_hash: str,
    ) -> bool:
        recorded = await self._our_posts_repo.record_our_post(
            ticket_id=context.ticket_id,
            body_hash=body_hash,
            body=body,
            channel=context.channel,
        )
        if not recorded:
            self._logger.info("our_post.duplicate_skip")
            return False
        self._logger.info("our_post.reply.saved")
        return True

    async def _post_comment(
        self,
        ticket_id: int,
        comment: str,
        *,
        public: bool,
    ) -> PostCommentResult:
        try:
            await self._zendesk_client.add_comment(ticket_id, comment, public=public)
            self._logger.info("comment.posted")
            return PostCommentResult(success=True)
        except httpx.HTTPError as error:
            error_text = str(error)
            self._logger.warning("http_error", extra={"error": error_text})
            return PostCommentResult(success=False, error=error_text)
        except Exception as exc:
            error_text = str(exc)
            self._logger.error("post_failed", extra={"error": error_text})
            return PostCommentResult(success=False, error=error_text)

    @staticmethod
    def _reply_body_hash(body: str) -> str:
        return hashlib.md5(body.encode()).hexdigest()
