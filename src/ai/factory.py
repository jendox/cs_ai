from functools import lru_cache

from src.ai.interfaces import LLMClient


# @lru_cache(maxsize=None)
# def get_llm_client(
#     provider: str,
#     model_name: str,
# ) -> LLMClient:
#     if provider == "google":
