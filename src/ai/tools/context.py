from dataclasses import dataclass

from src.ai.config import LLMRuntimeSettingsStorage
from src.ai.config.prompt import LLMPromptStorage
from src.ai.llm_clients import LLMClientPool
from src.ai.tools.executor import AmazonToolExecutor


@dataclass(frozen=True)
class LLMContext:
    client_pool: LLMClientPool
    runtime_storage: LLMRuntimeSettingsStorage
    prompt_storage: LLMPromptStorage
    amazon_executor: AmazonToolExecutor
