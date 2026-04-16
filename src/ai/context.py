import logging
from contextvars import ContextVar
from dataclasses import dataclass

from src.ai.amazon_mcp_client import AmazonMCPHttpClient
from src.ai.config import LLMRuntimeSettingsStorage
from src.ai.config.prompt import LLMPromptStorage
from src.ai.llm_clients import LLMClientPool
from src.brands import Brand

logger = logging.getLogger("llm_brand_ctx")


class MissingLLMBrandContext(RuntimeError):
    """Raised when brand-dependent tools are called without an LLM brand context."""


@dataclass(frozen=True)
class LLMContext:
    client_pool: LLMClientPool
    runtime_storage: LLMRuntimeSettingsStorage
    prompt_storage: LLMPromptStorage
    amazon_mcp_client: AmazonMCPHttpClient


@dataclass
class LLMCallContext:
    brand: Brand


llm_call_ctx: ContextVar[LLMCallContext] = ContextVar("llm_call_ctx")


def get_current_brand(required: bool = True, *, caller: str | None = None) -> Brand | None:
    """
    Safely get the current Brand from llm_call_ctx.

    Parameters
    ----------
    required : bool, default True
        If True, raise MissingLLMBrandContext when brand is not available.
        If False, return None and only log a warning.
    caller : str, optional
        Optional logical name of the caller (e.g. tool name) for better logging.

    Returns
    -------
    Brand | None
        The brand from the current LLM call context, or None if not set
        and required=False.

    Behavior
    --------
    - If the context variable is not set at all, or the brand is missing,
      logs a warning/error with enough information to debug.
    - If required=True (default), raises MissingLLMBrandContext so the
      problem is clearly visible in logs / error trackers.
    """

    context: LLMCallContext | None
    try:
        context = llm_call_ctx.get()
    except LookupError:
        context = None
    brand = getattr(context, "brand", None) if context is not None else None
    if brand is None:
        extra = {"caller": caller or "unknown", "ctx_present": context is not None}
        logger.error("llm_brand_ctx.missing", extra=extra)
        if required:
            raise MissingLLMBrandContext(
                f"LLM brand context is not set (caller={caller or 'unknown'})",
            )
        return None
    return brand
