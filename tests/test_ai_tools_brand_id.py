from __future__ import annotations

import pytest

from src.ai.context import LLMCallContext, llm_call_ctx
from src.ai.tools import _current_brand_id
from src.brands import Brand
from src.config import get_app_settings


@pytest.fixture
def superself_brand_ctx():
    token = llm_call_ctx.set(LLMCallContext(brand=Brand.SUPERSELF))
    try:
        yield
    finally:
        llm_call_ctx.reset(token)


def test_current_brand_id_uses_numeric_zendesk_id(superself_brand_ctx) -> None:
    brand_id = _current_brand_id(caller="get_product_by_asin")

    assert isinstance(brand_id, int)
    assert brand_id == get_app_settings().brand.id_for(Brand.SUPERSELF)
    assert brand_id != Brand.SUPERSELF.value
