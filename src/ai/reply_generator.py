import logging

from src.ai.config.prompt import LLMPromptStorage
from src.ai.llm_clients import LLMClientPool
from src.libs.zendesk_client.models import Ticket


class LLMReplyGenerator:
    def __init__(
        self,
        client_pool: LLMClientPool,
        prompt_storage: LLMPromptStorage,
    ) -> None:
        self._client_pool = client_pool
        self._prompt_storage = prompt_storage
        self.logger = logging.getLogger("llm_reply_generator")

    async def generate(self, ticket: Ticket, session_id: str) -> str:
        pass
