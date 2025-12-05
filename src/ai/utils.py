import re

from src.ai.config.runtime import TSettings
from src.ai.context import LLMContext
from src.ai.llm_clients import LLMClientInterface

SPLIT_PARTS = 2


class LLMJsonParseError(ValueError):
    """Raised when we cannot extract or parse JSON from LLM response."""


def extract_json_block(raw: str) -> str:
    """
    Extract a JSON object from an LLM response.

    Handles cases like:
        ```json
        {...}
        ```
    or any text that contains a single JSON object.
    """
    if not raw:
        raise LLMJsonParseError("Empty response from LLM")

    text = raw.strip()

    # If it's a fenced code block (``` or ```json)
    if text.startswith("```"):
        # Remove first fence line
        # e.g. ```json\n{...}\n```  -> {...}\n```
        parts = text.split("\n", 1)
        if len(parts) == SPLIT_PARTS:
            text = parts[1]
        # Remove closing fence at the end if present
        if text.endswith("```"):
            text = text[: -3].strip()

    # At this point text may содержать чистый JSON или какой-то мусор вокруг.
    # Попробуем вытащить JSON-объект по первой '{' и последней '}'.
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match:
        raise LLMJsonParseError("No JSON object found in LLM response")

    json_str = match.group(0).strip()
    if not json_str:
        raise LLMJsonParseError("Empty JSON object extracted from LLM response")

    return json_str


def resolve_llm_client_and_cfg(
    llm_context: LLMContext,
    settings: TSettings,
) -> tuple[LLMClientInterface, TSettings]:
    """Resolve provider, model and return (client, final_cfg)."""
    llm_settings = llm_context.client_pool.llm_settings

    provider = settings.provider or llm_settings.default_provider
    provider_settings = llm_settings.get_provider_settings(provider)
    model = settings.model or provider_settings.model

    final_cfg = settings.model_copy(
        update={
            "provider": provider,
            "model": model,
        },
    )
    client = llm_context.client_pool.get_client(provider)

    return client, final_cfg
