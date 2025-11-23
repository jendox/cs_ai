from dataclasses import dataclass

from src.ai.amazon_mcp_client import AmazonMCPHttpClient
from src.ai.config import LLMRuntimeSettingsStorage
from src.ai.config.prompt import LLMPromptStorage
from src.ai.llm_clients import LLMClientPool


@dataclass(frozen=True)
class LLMContext:
    client_pool: LLMClientPool
    runtime_storage: LLMRuntimeSettingsStorage
    prompt_storage: LLMPromptStorage
    amazon_mcp_client: AmazonMCPHttpClient
