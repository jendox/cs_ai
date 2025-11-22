import logging
import re
from typing import Any

from src.ai.tools.amazon_tools import AMAZON_TOOLS
from src.libs.amazon_client.client import AsyncAmazonClient

AMAZON_ORDER_ID_RE = re.compile(r"^\d{3}-\d{7}-\d{7}$")


class ToolExecutionError(Exception): ...


def is_amazon_order_id(order_id: str | None) -> bool:
    if not order_id:
        return False
    return bool(AMAZON_ORDER_ID_RE.fullmatch(order_id.strip()))


class AmazonToolExecutor:
    def __init__(self, client: AsyncAmazonClient) -> None:
        self._client = client
        self.tools = AMAZON_TOOLS
        self.logger = logging.getLogger("amazon_tool_executor")

    def _validate_order_id(self, raw_id: Any) -> str:
        order_id = (raw_id or "").strip()
        if not is_amazon_order_id(order_id):
            self.logger.warning("invalid_amazon_order_id", extra={"order_id": order_id})
            raise ToolExecutionError(f"invalid_amazon_order_id: {order_id}")
        return order_id

    async def execute(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> str:
        if name in {"get_order", "get_order_items", "get_full_order"}:
            order_id = self._validate_order_id(arguments.get("order_id"))
            self.logger.info("tool_call", extra={"data": {"name": name, "order_id": order_id}})
            if name == "get_order":
                order = await self._client.get_order(order_id)
                return order.model_dump_json()
            if name == "get_order_items":
                items = await self._client.get_order_items(order_id)
                return items.model_dump_json()
            if name == "get_full_order":
                order = await self._client.get_full_order(order_id)
                return order.model_dump_json()
        raise ToolExecutionError(f"Unknown tool: {name}")
