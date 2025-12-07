from src.ai.config.prompt import LLMPromptKey
from src.libs.zendesk_client.models import Brand


def _build_brand_map() -> dict[str, Brand]:
    mapping: dict[str, Brand] = {}
    for brand in Brand.supported():
        mapping[brand.name.lower()] = brand
        mapping[str(brand.value).lower()] = brand
    return mapping


_BRAND_MAP = _build_brand_map()


def _build_prompt_key_map() -> dict[str, LLMPromptKey]:
    mapping: dict[str, LLMPromptKey] = {}

    def add(key_: LLMPromptKey, *aliases: str) -> None:
        for alias in aliases:
            mapping[alias] = key_

    add(LLMPromptKey.INITIAL_REPLY, "initial", "initial_reply", "init")
    add(LLMPromptKey.FOLLOWUP_REPLY, "followup", "followup_reply", "follow-up")
    add(LLMPromptKey.CLASSIFICATION, "classification", "class", "clf")

    for key in LLMPromptKey:
        mapping[key.value.lower()] = key
        mapping[key.name.lower()] = key

    return mapping


_PROMPT_KEY_MAP = _build_prompt_key_map()


def parse_brand_token(token: str) -> Brand | None:
    normalized = token.strip().lower()
    return _BRAND_MAP.get(normalized)


def allowed_brand_tokens() -> list[str]:
    return sorted(set(_BRAND_MAP.keys()))


def parse_prompt_key_token(token: str) -> LLMPromptKey | None:
    normalized = token.strip().lower()
    return _PROMPT_KEY_MAP.get(normalized)


def allowed_prompt_key_tokens() -> list[str]:
    return sorted(set(_PROMPT_KEY_MAP.keys()))
