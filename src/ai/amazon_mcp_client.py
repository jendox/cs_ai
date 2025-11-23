from __future__ import annotations

from typing import Any, Dict

import httpx


class AmazonMCPHttpClient:
    """
    Minimal HTTP client for calling tools on the Amazon SP-API MCP server.

    This client talks to the MCP server over HTTP and exposes a single
    high-level method `call_tool`, which you can use from Gemini tools.

    The exact wire protocol depends on your MCP implementation.
    If you are using a specific MCP client library (e.g. gofastmcp),
    you can replace this class with a thin wrapper around that library.
    """

    def __init__(self, base_url: str) -> None:
        # Example: "http://127.0.0.1:9000"
        self._base_url = base_url
        self._client = httpx.AsyncClient(base_url=base_url, timeout=30.0)

    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """
        Call a tool on the MCP server and return its JSON result as a dict.

        Parameters
        ----------
        name : str
            MCP tool name, e.g. "get_order", "get_order_items" or "get_full_order".
        arguments : dict
            JSON-serializable arguments passed to the tool.

        Returns
        -------
        dict
            Parsed JSON response from the MCP tool.

        Notes
        -----
        - Adjust request path and payload format to match your MCP HTTP transport.
        - In this example we assume a simple JSON RPC-like structure.
        """
        # Ниже – один из возможных вариантов, просто как пример:
        payload = {
            "tool": name,
            "arguments": arguments,
        }
        response = await self._client.post("/tools/call", json=payload)
        response.raise_for_status()
        data = response.json()

        # Здесь предполагается, что MCP ответит чем-то вроде:
        # { "result": {...} }
        if isinstance(data, dict) and "result" in data:
            return data["result"]
        return data
