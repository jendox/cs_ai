from typing import Any

AMAZON_TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_order",
        "description": "Get short summary of an Amazon order (no line items) by AmazonOrderId.",
        "parameters": {
            "type": "object",
            "properties": {
                "order_id": {
                    "type": "string",
                    "description": "AmazonOrderId, e.g. '206-5253111-7078766'.",
                },
            },
            "required": ["order_id"],
        },
    },
    {
        "name": "get_order_items",
        "description": "Get all line items (products) for a given Amazon order.",
        "parameters": {
            "type": "object",
            "properties": {
                "order_id": {
                    "type": "string",
                    "description": "AmazonOrderId, e.g. '206-5253111-7078766'.",
                },
            },
            "required": ["order_id"],
        },
    },
    {
        "name": "get_full_order",
        "description": "Get both order summary and line items of an Amazon order.",
        "parameters": {
            "type": "object",
            "properties": {
                "order_id": {
                    "type": "string",
                    "description": "AmazonOrderId, e.g. '206-5253111-7078766'.",
                },
            },
            "required": ["order_id"],
        },
    },
]
