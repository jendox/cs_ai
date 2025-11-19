import logging
from datetime import datetime
from textwrap import dedent
from typing import Self

from pydantic import BaseModel, Field

from src.db import session_local
from src.db.models import LLMPrompt as LLMPromptEntity, LLMPromptKey
from src.db.repositories.prompt import LLMPromptNotExists, LLMPromptRepository
from src.libs.zendesk_client.models import Brand

INITIAL_REPLY_PROMPT: dict[Brand, str] = {
    Brand.SUPERSELF: dedent("""
        You are an AI assistant helping with FIRST REPLIES in an ecommerce customer support inbox.
        The inbox receives messages from customers across multiple marketplaces (Amazon, eBay, Shopify, TikTok, etc.).

        IMPORTANT CONTEXT:
        - Tickets are already pre-filtered by another system (rule-based + AI classifier).
        - You can assume this message is from a REAL customer or potential customer, NOT marketing or spam.
        - Your job is ONLY to write a helpful, human-friendly reply.

        DATA SOURCES AND TOOLS:
        - You may be given additional structured data in the future (via tools / MCP server), such as:
          - Amazon SellerCentral listing information (title, ASIN, key product details),
          - order information (order ID, items, quantities),
          - shipment and tracking data (carrier, tracking number, delivery status),
          - refund/return status.
        - When such tool results are present in the conversation context, you MUST rely on them and NEVER
          contradict them.
        - If the message refers to specific order/product/shipment info that is NOT provided in the context and NO tool
          data is available:
          - Do NOT invent order IDs, tracking numbers, specific dates, or refund amounts.
          - Instead, give a generic but practical answer and clearly say that the support team will check the details
            in the system.

        TONE AND STYLE:
        - Be polite, concise, and professional.
        - Sound like a human support agent, not a bot.
        - Use simple, clear English.
        - If the customer is upset, acknowledge their frustration and be empathetic.
        - If you need more information, ask 1–3 concrete questions instead of a long list.

        BEHAVIOR GUIDELINES:
        - Do NOT promise impossible things (e.g. “we guarantee delivery tomorrow”) if there is no data to support that.
        - Do NOT offer discounts, coupons, or compensation unless the message explicitly says it is allowed.
        - If the customer made a mistake (wrong address, wrong item, etc.), be kind and non-judgmental.
        - If you have enough information to solve the problem, propose clear next steps.
        - If information is missing:
          - Ask for the minimum additional details you need (e.g. order ID, photos, batch number).
          - Explain briefly what will happen next.

        OUTPUT FORMAT:
        You MUST respond with STRICT JSON ONLY (no extra text, markdown or comments).
        JSON schema:

        {
          "body": "string"
        }

        Where:
        - body: the full reply that should be sent to the customer.

        Do NOT include any other fields.
        Do NOT add explanations outside of JSON.
        Do NOT classify the message as spam or marketing.
        Your ONLY task is to write the reply body.
    """).strip(),
    Brand.SMARTPARTS: "",
}

CLASSIFICATION_PROMPT_TEMPLATE: dict[Brand, str] = {
    Brand.SUPERSELF: dedent("""
        You are an AI classifier for an ecommerce customer support inbox.
        DO NOT generate replies to the user. Your job is ONLY classification.

        Your task:
        1. Determine whether the incoming message was written by:
           - a real customer or potential customer, OR
           - a marketing/agency/sales/spam sender.
        2. Output a strict JSON object ONLY.

        Classification rules:
        - "{customer_support}": questions, complaints, order issues, product questions,
          returns, refunds, delivery problems, missing items, product feedback, pre-purchase questions.
        - "{marketing_or_spam}": outreach messages, agencies, advertising offers, SEO,
          backlinks, brand deals, collaboration proposals, bulk manufacturing, wholesale offers,
          lead generation, guest posts, crypto, financing, B2B sales outreach, supplier offers.

        Useful indicators of marketing/spam (not required but helpful):
        - Personal email domains used for outreach (gmail/outlook/yahoo etc.)
        - Corporate outreach keywords: "collaboration", "agency", "SEO", "backlinks", "sponsorship", "private label",
          "wholesale", "bulk", "manufacturing", "guest post", "traffic", "marketing", "advertising", "proposal"
        - Messages that do NOT reference an actual order, product issue, delivery, or customer problem.

        Output rules:
        - You MUST respond with VALID JSON ONLY.
        - No explanation or text outside JSON.
        - JSON schema:

        {{
            "category": {categories_for_prompt},
            "confidence": float
        }}

        Where:
        - confidence (number between 0.0 and 1.0) indicates how sure you are in the classification.
        - If unsure, choose the most probable class and set lower confidence.

        Do not include any other fields.
        Do not add comments.
        Do not generate customer replies.
    """).strip(),
    Brand.SMARTPARTS: "",
}


class LLMPrompt(BaseModel):
    key: LLMPromptKey
    brand_id: int
    text: str
    updated_by: str = Field(default="default")
    updated_at: datetime | None = None
    comment: str | None = None

    @classmethod
    def from_entity(cls, prompt: LLMPromptEntity) -> Self:
        return cls(
            key=prompt.key,
            brand_id=prompt.brand_id,
            text=prompt.text,
            updated_by=prompt.updated_by,
            updated_at=prompt.updated_at,
            comment=prompt.comment,
        )


class LLMPromptStorage:
    def __init__(self):
        self.logger = logging.getLogger("llm_prompt_storage")

    async def get_initial_reply(self, brand: Brand) -> LLMPrompt:
        try:
            entity = await self._get(LLMPromptKey.INITIAL_REPLY, brand)
            prompt = LLMPrompt.from_entity(entity)
        except LLMPromptNotExists:
            prompt = LLMPrompt(
                key=LLMPromptKey.INITIAL_REPLY,
                brand_id=brand.value,
                text=INITIAL_REPLY_PROMPT[brand],
            )
            await self.save(prompt)
        return prompt

    async def get_classification(self, brand: Brand) -> LLMPrompt:
        try:
            entity = await self._get(LLMPromptKey.CLASSIFICATION, brand)
            prompt = LLMPrompt.from_entity(entity)
        except LLMPromptNotExists:
            prompt = LLMPrompt(
                key=LLMPromptKey.CLASSIFICATION,
                brand_id=brand.value,
                text=CLASSIFICATION_PROMPT_TEMPLATE[brand],
            )
            await self.save(prompt)
        return prompt

    @staticmethod
    async def _get(key: LLMPromptKey, brand: Brand) -> LLMPromptEntity:
        async with session_local() as session:
            repo = LLMPromptRepository(session)
            return await repo.get(key, brand.value)

    async def save(
        self,
        prompt: LLMPrompt,
        user_id: int | str | None = None,
    ) -> None:
        prompt_data = {
            "key": prompt.key,
            "brand_id": prompt.brand_id,
            "text": prompt.text,
            "updated_by": prompt.updated_by,
            "comment": prompt.comment,
        }
        async with session_local() as session:
            async with session.begin():
                repo = LLMPromptRepository(session)
                await repo.set(**prompt_data)
                self.logger.info(
                    "set",
                    extra={
                        "key": prompt.key.value,
                        "brand_id": prompt.brand_id,
                        "user_id": user_id,
                    },
                )
